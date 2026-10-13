#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Single-cube local pick-and-place cycle, run ON THE LAPTOP against the running
NORMAL-mode verify-only motor host. ONE entry point for the local loop

    SMALL BOUNDED MOVE -> WRIST CAPTURE -> FROZEN PERCEPTION -> DECIDE

from a non-contact pregrasp above the cube to a verified placement. It adds no
motion machinery: every arm move goes through scan_world.run_scan_world (plan,
approval digest bound to the fresh start pose, base-zero checks around every
segment, arrival and measured-height checks, wrist capture, observation store)
and scan_arm_motion's bounded interpolation; every Cartesian target comes from
cartesian_lift's proven solvers behind hybrid_pick_place's proven gate; the
gripper uses the physically proven open / latch values.

Actions (each is planned from the FRESH measured pose, printed, then executed
in the same call unless --plan-only):
  observe                 capture + perception only, no command
  pan:DEG                 pan alone, |DEG| <= 6, only while the jaws are above the cube top (or while carrying)
  move:FORWARD_MM,DZ_MM   one Cartesian step from the measured pose, pan held, tool orientation kept
  grasp:FORWARD_MM,DZ_MM  final approach: the PROVEN grasp geometry shifted outward / up, at the measured pan (open gripper)
  grip:latch | grip:open  the gripper alone, arm joints held at their measured values
  base:SIGNED_S           ONE bounded base pulse (+ forward / - reverse, 0.2..0.8 s at 0.05 m/s) from the low scan pose, measured against the pad
  exit                    back to the saved low scan pose (gripper open, nothing held)
  scan                    the scene scan of the SEARCH stage (policy_search.scene_scan): refused before any motion (exit 3),
                          nothing commanded, until that stage builds it behind its words
  cycle                   the full state machine below, deciding every step from perception

Physical predicates (a state only advances on evidence, never on command completion):
  ALIGNED        the cube, propagated to grasp depth about the measured image nadir, lies inside the jaw capture window
  STRADDLING     the measured tool is at grasp height and the cube is still seen between the jaw columns
  OBJECT_SECURED the gripper stalled ABOVE its empty-latch value AND, after a verification lift, the cube is still seen
                 between the jaws at unchanged scale (it rose with the gripper)
  RELEASE ADMITTED the pad-relative evidence (the cube centre measured in PAD_FRAME at SEARCH, moved by every MEASURED base
                 displacement, plus only the discounted FK travel from the grasp radius) puts the cube centre at least its
                 half-diagonal plus 20 mm from every pad edge in the worst case. FK alone never admits a release.
  PLACED         after release and retreat the cube is detected and its contact point lies inside the pad by the margin

PLACEMENT: arm-only where the loaded reach achieves that margin. Otherwise the arm folds to the low scan pose WITH the cube
(the only pose from which the pad print fixes PAD_FRAME), the base advances in bounded pulses of the proven gateway
primitive with the arm HELD, every pulse is followed by stop, base-zero confirmation and a pad re-observation that measures
it, the cube is lowered at about the grasp radius, and after the release the base returns to its start the same way.

SEARCH SWEEP (under the word search_sweep only, UNQUALIFIED ON HARDWARE below): when the low-pose frame verifies no cube
with a usable contact pixel, SEARCH pans the wrist through two sectors,
LEFT then RIGHT, SEARCH_SWEEP_DEG (25 degrees) either side of the FRESH measured pan, built by the proven scan tooling
(run_stage1_scan.sweep_poses: lift, elbow and wrist_flex held exactly) and each run through run_pose with every check an arm move
has (the abort flag per segment, base zero before and after, arrival, the measured height, the wrist ceiling); RIGHT is reached
through the centre pose, because the planner's entry bound (scan_poses.MAX_ENTRY_PAN_SWEEP_DEG, 30) refuses a wider pan in one
call, and 25 is the widest sector whose arrival, up to ARRIVAL_TOLERANCE_DEG past its target, can still plan the transit back.
No sector lies outside plus or minus 30 degrees of the centre pan. Every sector frame is perceived by the unchanged frozen
perception; the first frame with a verified cube that has a contact pixel and a valid PAD_FRAME is the SEARCH frame from then
on, and its measured pan is the pan the coarse pregrasp and a mission selection are computed from (both unchanged, linear in
it). One state per sector, SEARCH_SWEEP, names the sector, the pan, the outcome (cube, no_cube, pad_frame_invalid) and the
observation. No usable sector: the arm returns to the centre pose through run_pose and the cycle fails
search_no_cube_after_sweep (exit 5, the arm holds).
RETURN BOUND: the same entry bound limits what the sweep can complete. EXIT (and the HOLD_OBSERVE rise of a base advance)
plans one pose at the saved low pose's pan from wherever the arm is, so a cube whose coarse pregrasp pan lies more than
RETURN_PAN_BOUND_DEG (25, the entry bound less the arrival tolerance) from that pan is refused at SEARCH with
search_cube_beyond_return_bound, before anything is picked up, and a cube whose coarse pan lies more than the entry bound from
the sector the arm stands at is refused with search_cube_beyond_pregrasp_entry_bound; through ALIGN every pan trim is held to
the same band before it is commanded (align_trim_beyond_return_bound, the arm at the pregrasp, nothing gripped). Stated
plainly: the band the cycle can complete is 25 degrees of pan either side of the saved low pose, and by the coarse mapping
(11.8 px per degree about column 553) the centre frame itself maps about 47 degrees to the left and 62 to the right of it,
so a sector frame can only complete a cube the centre frame already had in view but did not verify there (at the edge of
the frame, or a recognition that failed at that pan); a cube beyond the band is found, stated, and left where it lies,
the arm at the sector, a pan every return can plan from. UNQUALIFIED ON HARDWARE: no recorded run has panned the low pose 25
degrees either way or fixed PAD_FRAME from there; the sweep is exercised on the scripted World and machine only, until the
attended qualification of docs/autonomous-demo/one-block-demo-2026-09-26.md (T5): a plan-only sweep, then one attended sweep
with the block off-centre, then three starts. So the sweep runs only under the search stage's word search_sweep (declared,
not qualified; a LIVE run naming it needs the attended label). With no word a low-pose frame with no usable cube is refused
before any motion, in a LIVE run, a rehearsal and a plan-only run alike: search_no_cube in a single-cube run, as the
2026-09-20 tag refused it, and in a mission run the target selection's own refusal of that frame (mission_binding_no_candidate
when it lists no cube), as before the sweep was added.

THE ARM PATH NEVER COMMANDS THE BASE: every arm command is built by the manipulation action adapter (base exactly zero) and
re-checked; the base moves only through make_move_base, never while an arm segment runs.

THE HOST COMMAND LEASE, off unless the environment names one. A motor host started with bin/pi/host_boundary.py hands its
loop only commands that carry the live lease as two NUMERIC keys, and drops every other command one by one. The runtime
acquires that lease and names it here, in two variables this module reads once, at start, and nothing else reads at all.
They are the pair demo_runtime.supervisor exports (LEASE_ENV_GENERATION, LEASE_ENV_SESSION); a controller that read any
other pair would get nothing from that runtime, and a test pins these two names against that module's source:

  KC_HOST_LEASE    the generation the runtime was granted, a whole number, 1 or more
  KC_HOST_SESSION  the host session that granted it, as the host reports it: plain decimal text

With NEITHER set nothing is attached and every command is byte for byte the command it was. With BOTH set every action this
run sends carries the two keys of demo_runtime.lease_client.command_metadata: the ARM through the robot client run_pose and
grip send on, the BASE through every LeKiwiTransport a pulse builds. In both paths the keys are added on a COPY at the
moment the action leaves the client, after every guard and clamp, so no caller's dict is touched and nothing else in the
payload changes. With ONE of them set, or a pair the host could never have issued, the run is REFUSED before any motion
(exit 3): discovering a rejected command at the first arm move, with a cube in the jaws, is the worse way to learn it.
The two values are read from the environment and never printed, recorded or emitted: they name a live robot host.

MISSION MODE, off unless the environment names a mission target. KC_MISSION_TARGET names a file the runtime wrote into the
run directory; autonomy/manipulation/target_binding.py owns its schema and reads it, strictly, once, at start. With the
variable ABSENT nothing of this is consulted and every hook below is a no-op: the cycle is the legacy cycle, command for
command. With it, this run carries exactly ONE target of a multi-cube mission. A file that does not parse, or a target this
invocation cannot carry (not --do cycle, plan-only, another run's id, a capability carried off loopback, no qualified
claim and match radii), is REFUSED before the robot is constructed (exit 3). Every other mission refusal is a CycleFailed
(exit 5, the arm holds), taken before the motion it would have allowed:
  SEARCH     every verified cube of the low-pose frame is listed (autonomy/perception/cube_candidates.py, run AFTER the
             unchanged frozen perception) and target_binding picks the one candidate this target is bound to, in PAD_FRAME,
             with the radii the target file carries. A block on the pad (the operator's pad policy of 2026-09-29: a real
             one, or the pad's print read as one) is never picked up, never bookkept and not counted against the run by
             the selection, and DONE_ALREADY_PLACED is a refusal here. Three ways a block on the pad still fails a run
             remain, named residuals (target_binding, THE PAD POLICY): the listing's pass budget (passes on blocks on the
             pad are free up to target_binding.LISTING_ON_PAD_EXEMPT_PASSES, 9, and a frame past that is still
             incomplete); the SEARCH SWEEP, which does not run when the low-pose frame verifies a block on the pad, so
             SEARCH lists only that frame and refuses mission_binding_no_candidate; and ALIGN below, whose count of
             verified cubes includes blocks on the pad (align_more_than_one_cube_verified). The first execution binds
             every declared target; an auto target takes the nearest feasible block off the pad (target_binding, THE
             SELECTION RULE). The selection is written
             back (stage "bound") before the first motion past SEARCH (the SEARCH SWEEP, when the low-pose frame
             verifies no cube, pans the wrist before any cube is selected).
  MODE       the placement mode this run drives (target_binding.MissionTarget.run_mode): a declared target's declaration,
             which its cube must measure (D5: a target that declares no capability and measures BASE_ADVANCE is refused
             at SEARCH, never driven), or for an AUTO target (requires_capability "auto": one block, the robot decides)
             the mode its binding MEASURED, so SEARCH takes what it measured and refuses nothing on that account. Once
             the cube is held, a plan from the measured grasp in another mode ends the run with the cube held
             (mission_mode_changed_while_held), with one exception: an auto target bound ARM_ONLY whose plan flips to
             BASE_ADVANCE in a run that CARRIES the capability is driven as a capability run drives it. In a run that
             carries none the flip, like the arm-only fallback, ends the run with the cube held: the operator resets.
  CAPABILITY the need for the capability follows the cube's MEASURED reachability: after the selection is written back, a
             cube that measures BASE_ADVANCE is refused before PREGRASP, mission_capability_required when this run carries
             no capability (the runtime runs a first-execution capability target, and every auto target, without it and
             decides from this refusal), and mission_capability_observation_unavailable while the observation source answers that no
             mapping to the capability's contract exists (autonomy/motion/target_observation_source.py; under contract
             v2 it answers that one exists, and whether THIS run has an observation is decided per pulse, below).
             THE OBSERVATION THE CAPABILITY IS FED is contract v2 (kc.target_observation/2,
             autonomy/schemas/target_observation.v2.schema.json): frame "anchor_reach", whose origin is the held cube's
             anchor in PAD_FRAME and whose x axis is the reach axis, and x_m the remaining bounded advance
             base_advance_needed_mm / 1000, exactly the quantity advance_base pulses on. It is resolved by
             target_observation_source.resolve from three things this controller keeps: the approach state (_approach:
             the anchor, the axis, the remaining advance and the pad validity figures of the view they came from, kept
             by advance_base from a STAMPED observe() taken right after HOLD_OBSERVE for a run that carries the
             capability, and again after every measured pulse; None outside advance_base), the latest stamped capture
             (_capture: id, monotonic stamp, motion epoch, and whether the base read at rest), and the
             perception budget (the desktop perception deadline; a run without one has no observation). The relay
             publishes it with its own stamp every period, and only an answer to that publication drives the base.
  ALIGN      refused when the pregrasp view verifies more than one cube (align_more_than_one_cube_verified), a block
             on the pad in that view included (a named residual of the pad policy: the count is the recognizer's).
  PLACEMENT  the placement mode must be the declared one, at SEARCH and again once the cube is held. A run that carries no
             capability never drives the base (the arm-only fallback to the base is refused with the cube held). A run
             that carries it drives FORWARD only through the Cake-managed capability (autonomy/motion/capability_pulse.py,
             relayed into the same gateway), publishing only what target_observation_source gives: the in-process
             approach controller and its synthetic observation are never imported or constructed for it. The base
             return is the proven reverse primitive, as before.
  PLACED     attributed by THE RELEASE AGREEMENT (target_binding): a block on the pad within RELEASE_AGREEMENT_MM of this
             run's own release segment (release_segment_pad of its RELEASE_ADMISSION), whatever was there before (operator
             ruling (ii) of 2026-09-29, with its accepted residual). Every other block on the pad is ignored; none at the
             release is mission_placed_no_cube_at_release_point.
  STOP       once the stop flag is set no perception starts and the result of one already running starts nothing; the
             read-only re-observation that measures where a base pulse stopped is the one exception. A gripper move that
             was aborted is not followed by an observation. The capability relay reads the same flag again after its
             seconds of Cake reads and link wait, before it builds a transport: a stop seen there commands no pulse.
The ending is written back (stage "finished") echoing mission_id, target_id and run_id, for every status _run returns once
the Cycle exists (0, 3, 4 and 5); a write that fails is printed and changes no status. NOTHING is written for a refusal
before the robot is constructed (the file may belong to another run: writing its result path could overwrite that run's
result), nor when the process ends by what _run does not catch (the robot cannot be built or connected, the live pose never
arrives, a traceback). The runtime then reads no placement from the run, which is the fail-closed reading.

THE ADDITIVE POLICIES (the operator's directive of 2026-09-30: many policies for different decisions, steps and stages;
driving avoided when unnecessary). Each is a module of its own behind its own word, OFF by default and unqualified (a LIVE
run names it only under the attended label), beside the five stage modules, which do not change a byte:
  arm_reach, advance_then_reach   placement policies (autonomy/manipulation/policy_arm_reach.py, policy_advance_then_reach.py):
             the arm alone to the loaded reach cap (for a block the proven policy places, and for any other whenever a plan
             of the run under it fits the advance budget, its commands stopping at the proven loaded reach and its plan
             counting only on the out they measure under the record, its release keeping the proven rule's lagging-arm
             buffer; the reach model's measured-out cap for a block no such plan serves: the operator's rulings of
             2026-10-01 and 2026-10-02, placement_policy THE LOADED REACH CAP), or the smallest measured advance the arm's
             reach then needs. Under either word SEARCH plans every named policy beside the proven placement_plan and binds the one
             with the least base motion (a tie within the advance loop's tolerance goes to the proven policy; the placement
             selector, autonomy/manipulation/placement_selector.py). Once the cube is held the placement executor
             (autonomy/manipulation/placement_executor.py) plans them again from the measured grasp and hands the cube to
             the chosen one: its closed-loop carry on the measured out (autonomy/manipulation/reach_carry.py), after the
             frozen measured advance for advance_then_reach, then the proven admission and release. A release refused
             at the reach cap brings the loaded arm back to the carry pose in bounded steps read one by one, only where
             every step stays inside what the records show the loaded arm doing (THE WAY BACK UNDER LOAD), and then, in a
             run that may move the base, the same run goes on with the next policy that moves the base (advance_then_reach,
             then the proven stage itself), before any refusal: the lead's decision 3, which runs only where the way back
             can step. Where it cannot, the arm holds the cube where it stands, the gripper closed, and the run ends for
             the operator (reach_way_back_held), its record saying why the in-run fallback did not run (the lead's ruling
             of 2026-10-02 on operator question Q7: keep the hold until a loaded session is measured; no loaded move
             outside the recorded envelope). On the recorded loaded behaviour a loaded arm lags its commands, so that hold
             is the expected ending of a release refused after a lowering, and the fallback runs from a refusal that leaves
             the arm at the carry height where it was commanded. A mission run may move the base only when it carries the capability, which the runtime gives
             only before a spawn: so an AUTO target that SEARCH binds to the arm alone, in a run that carries none, is
             refused at SEARCH before anything is picked up when the fallback that binding would go on with moves the base
             (mission_fallback_capability_required, _mission_fallback_capability_gate; the selection is the arm alone), and
             the runtime's one respawn carries the capability, where the arm alone places the block with no base motion or,
             should its release be refused, the fallback moves the base only if the way back brings the loaded arm to the
             carry pose (a loaded arm the way back holds stays where it stands, the cube held for the operator), with an
             advance planned again from where the run then stands. The same holds where SEARCH's tie at no base motion went to the proven arm-alone
             plan and that plan needs the base at SEARCH's planning grasp (the executor then hands the held cube to an
             additive policy); and an auto target's run that carries no capability and that SEARCH bound to the proven
             policy is never handed to an additive policy once held (placement_executor, THE PROVEN TIE). A run that never
             drives the base ends a refused arm_reach with the arm retracted and the cube held: a LIVE run without a
             mission target (LIVE_FORWARD_EXCLUDED), or an auto target's run that carries no capability where SEARCH saw
             no fallback that moves the base (reach_arm_only_insufficient_while_held). The frozen advance
             loop calls base_advance_needed_mm by name, and under these words only it is rebound for the length of one
             advance (_advance_rule). SEARCH still refuses, before anything moves, a least-motion plan of a policy a build
             plans without executing (search_placement_policy_without_executor): none, in this build.
  multi_block  the SEARCH word (autonomy/manipulation/block_selection.py, THE RULE): among several blocks off the pad that the
             proven policy places, the binding takes the one the proven rule takes, the one nearest the pad, whatever
             base the blocks' plans name (the lead's decision of 2026-10-04: the installed arm-only carry places no block
             under the recorded loaded models, so an arm-alone block taken over the installed rule's base-placed block
             lost placements 31c546c makes); where the proven policy places none of them, the one whose plan moves the
             base least; and SEARCH from the scene (scene_search) takes a block of several off the pad by the same rule,
             where the proven scene search refuses the scene (the scene search is lent to it for the length of SEARCH
             only, _placement_policies_at_search).
             Under any of these words the mission binding also asks SEARCH's pickup bands (the return and entry pan bounds,
             the near block band) of every listed block with a placement, and never binds one they refuse while another
             of its tier passes them (Cycle._mission_pickup_band, block_selection THE PICKUP BANDS AT THE BINDING). Among
             several listed blocks, by either binding rule and in the scene search, it takes a block the proven policy
             refuses only where no listed block is one the proven policy places, the ones the bands refuse included
             (where the bands refuse every block of the first tier, the binding takes one of them and SEARCH refuses it
             before anything moves, as the installed controller does; among the blocks the proven policy refuses, one
             whose plan under the loaded reach cap still fits the budget at the record's largest grasp under-read comes
             before one whose plan fits only inside SEARCH's charge), and a block only the long stretch places (an
             additive plan under the reach model's cap, which the operator's ruling of 2026-10-02 keeps for a block no
             plan under the loaded reach cap serves) only where no listed block has the proven plan or one under the cap
             (block_selection THE BINDING TIERS, the lead's decision of 2026-10-03: on the recorded loaded behaviour
             such a block can end held on the stretched arm where the installed controller places the other block).
With none of these words every decision, record and command is the installed controller's (a differential test pins it
against commit 31c546c).
THE WORDS APPLY TO AUTO TARGETS ONLY (the lead's decision of 2026-10-01). A mission with a target that declares its mode
(it requires the capability, or declares that it does not), and so every mission with such a target beside an auto one, is
bound, planned and executed exactly as the installed controller binds, plans and executes it, whatever words the run names:
SEARCH plans with the proven placement_plan alone, binds by target_binding and searches the scene by the proven procedure
(additive_words_apply; _placement_policies_at_search lends nothing), and the held cube goes to the proven stage
(run_cycle never hands it to the placement executor). The words are still named in the run's record (PLACEMENT_ENVELOPE).
A differential test pins such missions under every word against commit 31c546c with none.

THE SYNTHETIC OBSERVATION IS OUT OF A LIVE RUN. The proven forward primitive (run_hybrid_pick_place._make_move_base_sustained)
feeds its in-process controller a fixed observation, x 1.0 m in the v1 "base" frame, and the operator ruled that observation
valid for rehearsal only. What a LIVE mission run feeds the capability instead is the v2 observation above (CAPABILITY):
a measured remaining advance from a stamped, stationary capture, or nothing. The runtime exports KC_RUNTIME_MODE ("live"
or "synthetic", demo_runtime.supervisor) to every run it starts. Under
"live", a run without a mission target never imports that primitive (its forward half is a refusal), refuses a forward
base action before the robot is constructed (exit 3), refuses at SEARCH, before PREGRASP, a cube whose placement needs the
base (synthetic_observation_excluded_from_live), and refuses a forward pulse wherever one would still be reached. The
reverse primitive, which commands a fixed reverse and no observation, is unchanged. Without the variable (a run started by
hand, as every recorded physical run was), nothing of this applies and the proven cycle is the proven cycle.

DESKTOP PERCEPTION, off unless the environment names it. The operator's one offboard exception: the complete per-frame
perception (OWLv2 and the pad fitting) may run on the desktop GPU; everything else stays here. The runtime on the robot sets
KC_PERCEPTION_REMOTE_PORT, KC_PERCEPTION_DEADLINE_S, KC_PERCEPTION_MODEL_REVISION and KC_PERCEPTION_MODEL_FILES_DIGEST (all four,
from its perception.remote configuration; demo_runtime.supervisor) only for a LIVE run on the robot. With the port ABSENT
nothing of this is consulted: the detector is made here and perceive runs here, exactly as before. With it, every frame this
run perceives is sent, as the stored PNG, to the desktop service through its reverse forward, and ONLY an answer that
autonomy/perception/remote_perception.py accepts is used: the record and overlay bytes are written where perceive writes
them and the cube proposals feed the cube listing; the controller's decisions are made here, on those bytes, unchanged.
  BEFORE THE ROBOT IS CONSTRUCTED the service is pinged and must state this tree's frozen perception code (the benchmark's
             bytes), this run's pad template and the configured model pin. A missing variable, a tree that is not the
             measured perception, a service that does not answer in time or answers as anything else is a refusal (exit 3).
  EVERY FRAME a missing, late (after this robot's own deadline), mismatched (request, robot or capture identity, model,
             code, template, the bytes or pixels received) or refused answer is a CycleFailed (exit 5, the arm holds):
             nothing is written as a result and nothing is decided on it. There is no retry and no local fall back.
  STOP       the request is waited for in slices of at most 0.1 s and abandoned the moment the stop flag is seen (exit 4,
             "stopped"); its answer, if it ever comes, arrives at a closed socket. The read-only re-observation after a
             base pulse is the one exception, as for the local stop gate: it runs to its answer or to its deadline.
The request names this run: the resident session it was admitted under (KC_RESIDENT_SESSION), the run and scan id, the
mission and target when there are any, and this process's pid; and the capture: the observation id, the stored file's
sha256 and the decoded pixels' digest. The camera service's session and sequence are not in the stored observation and are
sent as null. None of those names is printed or emitted: they travel only in the request and its local evidence file.
"""
import argparse
import contextlib
import functools
import hashlib
import importlib
import json
import math
import os
import re
import signal
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
for sub in ("bin/laptop/perception", "autonomy/perception", "autonomy/manipulation", "tools"):
    sys.path.insert(0, str(ROOT / sub))

import cartesian_lift  # noqa: E402
import run_stage1_scan as cli  # noqa: E402  (read_pose, PiCamera, perceive, the outgoing-command guard)
import target_binding  # noqa: E402  (standard library only: the mission target file, its predicates and the result file)
from action_adapter import freeze_wrist_roll, manipulation_action_to_robot_command  # noqa: E402
from base_rest import reads_at_rest  # noqa: E402  (the one base-at-rest rule, per wheel: operator ruling 2026-09-27)
from calibration_inventory import CalibrationInventory  # noqa: E402
from canonical_handoff import ABORTED, REFUSED_DELTA_TOO_LARGE, run_canonical_handoff  # noqa: E402
from hybrid_pick_place import GRIPPER_LATCH, GRIPPER_OPEN, JOINT_LIMIT_MARGIN_DEG, JOINT_LIMITS_DEG, KNOWN_GOOD_GRASP_POSE_DEG, _gate_cartesian_result  # noqa: E402
from observation_store import ObservationStore  # noqa: E402
from scan_arm_motion import make_bounded_move_arm  # noqa: E402
from scan_poses import MAX_ENTRY_PAN_SWEEP_DEG, SCAN_CONTROL_FPS, SCAN_JOINTS, SCAN_PAN_ABS_MAX_DEG, TRANSIT_SAMPLES, link_heights_m  # noqa: E402
from scan_world import ARRIVAL_TOLERANCE_DEG, ScanRefused, ScanWorldDeps, _base_is_zero, run_scan_world  # noqa: E402
import policy_align  # noqa: E402  (the PREGRASP and ALIGN stage policy)
import policy_grasp  # noqa: E402  (the GRASP stage policy)
import policy_placement  # noqa: E402  (the PLACEMENT stage policy)
import policy_return  # noqa: E402  (the RETURN stage policy)
import policy_search  # noqa: E402  (the SEARCH stage policy)
import stage_policy  # noqa: E402  (the stage seam: the context protocol, the controller view)
import placement_envelope  # noqa: E402  (the placement envelope solver: a refusal's binding limit, deficit and hint)
import placement_selector  # noqa: E402  (the additive placement policies side by side, and the least-motion choice among them)
import placement_executor  # noqa: E402  (the held cube placed by the additive placement policies, the proven stage among them, in order)
import block_selection  # noqa: E402  (the SEARCH word multi_block: which block of several off the pad a target binds)
import policy_align_left_clearance  # noqa: E402  (the ALIGN word align_left_clearance: the block kept 6 to 9 mm clear of the fixed jaw)
from stage_policy import CycleFailed  # noqa: E402  (one class for every catch: the controller, the stage policies, the tests)

# What every stage policy reads the controller's geometry gates, formulas and constants through (stage_policy.ControllerView):
# this module's own namespace, read by name at the moment a stage reads it.
_CONTROLLER_VIEW = stage_policy.ControllerView(globals())
_STAGE_POLICIES = (policy_search.POLICY, policy_align.POLICY, policy_grasp.POLICY, policy_placement.POLICY, policy_return.POLICY)  # in run_cycle's order
# THE ADDITIVE POLICIES (the operator's directive of 2026-09-30: "it is better to have many policies for different
# decisions/steps/stages"): each a policy module of its own, behind its own word, OFF by default and unqualified, beside the
# five stage modules, which do not change a byte: the placement policies arm_reach and advance_then_reach (placement_selector)
# and the SEARCH word multi_block (block_selection), and the ALIGN word align_left_clearance (policy_align_left_clearance).
# Their words are declared, qualified and refused like every stage's (THE PLACEMENT ENVELOPE below reads both tuples, at call
# time), and run.config names one only when the run names its word.
_ADDITIVE_POLICIES = tuple(module.POLICY for module in placement_selector.POLICIES) + (block_selection.POLICY, policy_align_left_clearance.POLICY)

LIFT_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex")
PAN_AXIS_XY = np.array(cartesian_lift._JOINT_GEOMETRY["shoulder_pan"][0][:2])
_GRASP_FK = cartesian_lift.forward_kinematics({**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0})
GRASP_TOOL_Z_M = float(_GRASP_FK[2, 3])
GRASP_TOOL_RANGE_M = float(np.hypot(*(_GRASP_FK[:2, 3] - PAN_AXIS_XY)))

# Bounds of ONE step. Floors are arm base_link FK z of the lowest link origin (wrist_roll 0, this project's convention).
STEP_MAX_DZ_M = 0.030
STEP_MAX_FORWARD_M = 0.060
STEP_MAX_PAN_DEG = 6.0
NON_CONTACT_FLOOR_M = GRASP_TOOL_Z_M + 0.027  # jaw tips above the cube top plane whatever the lateral alignment
CONTACT_FLOOR_M = GRASP_TOOL_Z_M - 0.015  # plausibility bound of the MEASURED pose near the ground, not a collision guard
LOWEST_TARGET_BELOW_GRASP_M = 0.002  # a step never TARGETS a tool height below the physically proven grasp height
PAN_NEEDS_TOOL_ABOVE_GRASP_M = 0.030  # open gripper: the pan only moves while the jaw tips are above the cube top plane
MAX_TILT_ERROR_DEG = 1.0
MAX_PATH_DEVIATION_M = 0.006  # the joint-linear path may leave the straight Cartesian line by at most this
GRIPPER_OPEN_TOLERANCE = 3.0
GRIPPER_HOLDING_BAND = (66.0, 72.0)  # latch command 66.8; a held 25.4 mm cube stalls the jaw near 68
# A jaw that reaches the latch value within this closed on NOTHING. The readback moves in whole steps of 100/1449
# (0.069); the recorded latch readbacks (2026-09-19..27, 11 empty, 14 held) are 969..976 steps empty and 984..991 held,
# so 66.8 + 0.87 = 67.67 sits half a step above 980, the class midpoint: no readback falls on it, 4 steps above the
# worst empty and 3 below the lowest hold (0.6 sat 0.043 above the worst empty, less than one step).
GRIPPER_EMPTY_LATCH_MARGIN = 0.87
GRIPPER_MOVE_S, GRIPPER_HOLD_S = 0.5, 0.75  # the proven values
SLOW_FACTOR = 2.0
HOME_FILE = ROOT / ".local-dev" / "calibration" / "scan-home-current.json"


def _straight_arm_elbow_deg() -> float:
    """The elbow_flex value at which the shoulder-to-wrist distance peaks: the arm's real kinematic singularity in THIS
    URDF joint convention (about -73.8 deg; at elbow_flex 0 the two links are about 106 deg apart)."""
    def extension(elbow: float) -> float:
        transform, origins = np.eye(4), {}
        for name in cartesian_lift.JOINT_ORDER:
            xyz, rpy = cartesian_lift._JOINT_GEOMETRY[name]
            transform = transform @ cartesian_lift._joint_transform(xyz, rpy, np.deg2rad(elbow if name == "elbow_flex" else 0.0))
            origins[name] = transform[:3, 3].copy()
        return float(np.linalg.norm(origins["wrist_flex"] - origins["shoulder_lift"]))

    angles = np.arange(-180.0, 180.0, 0.1)
    return float(angles[int(np.argmax([extension(a) for a in angles]))])


ELBOW_STRAIGHT_ARM_DEG = _straight_arm_elbow_deg()
TRUE_SINGULARITY_MARGIN_DEG = 20.0  # twice the proven gate's margin, applied where the singularity really is
_BAND_REASON = "singularity (0/180)"


def gate_step(solved: dict, start: dict, requested: dict):
    """The PROVEN gate, with its elbow guard corrected, not removed. The proven gate places the singularity at elbow_flex
    0 / 180; in this URDF convention the straight arm is at ELBOW_STRAIGHT_ARM_DEG and the reach grows smoothly through
    0 (verified offline 2026-09-19, independently confirmed). Every other refusal of the proven gate stands. When the
    0/180 band is its ONLY objection, the same intent is enforced at the real singularity with twice the margin; the
    caller applies the same bound to every sample of the interpolated path."""
    accepted, reason = _gate_cartesian_result(solved, start, requested)
    if accepted:
        return True, ""
    others = [part for part in reason.split("; ") if part and _BAND_REASON not in part]
    if others:
        return False, "; ".join(others)
    elbow = float(solved["joint_deg"]["elbow_flex"])
    if abs(elbow - ELBOW_STRAIGHT_ARM_DEG) <= TRUE_SINGULARITY_MARGIN_DEG:
        return False, f"elbow_flex target {elbow:.2f} deg is within {TRUE_SINGULARITY_MARGIN_DEG} deg of the straight-arm singularity ({ELBOW_STRAIGHT_ARM_DEG:.1f})"
    return True, f"proven gate objected ONLY with its 0/180 elbow band ({reason}); accepted under the corrected guard: elbow {elbow:.2f} is {abs(elbow - ELBOW_STRAIGHT_ARM_DEG):.0f} deg from the straight arm"


def tool_state(measured: dict) -> dict:
    """Where the FK tool is relative to the PROVEN grasp, from MEASURED joints."""
    transform = cartesian_lift.forward_kinematics({**{k: float(measured[k]) for k in SCAN_JOINTS}, "wrist_roll": 0.0})
    return {"out_mm": (float(np.hypot(*(transform[:2, 3] - PAN_AXIS_XY))) - GRASP_TOOL_RANGE_M) * 1000.0, "up_mm": (float(transform[2, 3]) - GRASP_TOOL_Z_M) * 1000.0,
            "pitch_deg": sum(float(measured[k]) for k in LIFT_JOINTS), "lowest_link_m": min(link_heights_m({k: float(measured[k]) for k in SCAN_JOINTS}).values())}


def path_report(start: dict, target: dict) -> dict:
    """The joint-linear path the executor will really follow, sampled: the endpoint being acceptable does not make the
    path acceptable. Reports the worst departure from the straight Cartesian line (in the arm plane), the lowest link on
    the way, the elbow range crossed and the closest approach to any joint limit."""
    points, lowest, elbows, limit_margin = [], [], [], 1e9
    for t in np.linspace(0.0, 1.0, TRANSIT_SAMPLES):
        joints = {k: float(start[k]) + t * (float(target[k]) - float(start[k])) for k in SCAN_JOINTS}
        position = cartesian_lift.end_effector_position({**joints, "wrist_roll": 0.0})
        points.append((float(np.hypot(*(position[:2] - PAN_AXIS_XY))), float(position[2])))
        lowest.append(min(link_heights_m(joints).values()))
        elbows.append(joints["elbow_flex"])
        limit_margin = min(limit_margin, min(JOINT_LIMITS_DEG[k] - abs(joints[k]) for k in JOINT_LIMITS_DEG))
    a, b = np.array(points[0]), np.array(points[-1])
    chord = b - a
    length = float(np.linalg.norm(chord))
    deviation = max(abs(float(chord[0] * (p[1] - a[1]) - chord[1] * (p[0] - a[0]))) / length for p in points) if length > 1e-6 else 0.0
    return {"max_deviation_m": deviation, "lowest_link_m": min(lowest), "elbow_range_deg": (min(elbows), max(elbows)), "min_joint_limit_margin_deg": limit_margin,
            "min_true_singularity_distance_deg": min(abs(e - ELBOW_STRAIGHT_ARM_DEG) for e in elbows)}


def cartesian_step_pose(measured: dict, forward_m: float, dz_m: float, holding: bool):
    """ONE bounded Cartesian step from the MEASURED pose (the proven pattern: every increment is solved fresh), pan held,
    tool orientation kept, accepted only by the proven gate. Raises ScanRefused with the reason otherwise."""
    if not (math.isfinite(forward_m) and math.isfinite(dz_m)) or abs(dz_m) > STEP_MAX_DZ_M or abs(forward_m) > STEP_MAX_FORWARD_M or (forward_m == 0.0 and dz_m == 0.0):
        raise ScanRefused(f"one step moves at most {STEP_MAX_FORWARD_M * 1000:.0f} mm forward/back and {STEP_MAX_DZ_M * 1000:.0f} mm up/down, got {forward_m * 1000:+.1f} / {dz_m * 1000:+.1f}")
    start = {**{k: float(measured[k]) for k in SCAN_JOINTS}, "wrist_roll": 0.0}
    solved = cartesian_lift.solve_cartesian_forward_and_z(start, forward_m, dz_m)
    accepted, reason = gate_step(solved, start, {"delta_forward_m": forward_m, "delta_z_m": dz_m})
    if not accepted:
        raise ScanRefused(f"the gate refuses this step: {reason}")
    if reason:
        print(f"GUARD NOTE: {reason}")
    if solved["orientation_error_deg"] > MAX_TILT_ERROR_DEG:
        raise ScanRefused(f"the tool orientation is not kept on this step (tilt error {solved['orientation_error_deg']:.2f} deg)")
    target = {k: round(float(solved["joint_deg"][k]), 3) for k in SCAN_JOINTS}
    target["shoulder_pan"] = float(measured["shoulder_pan"])
    after = tool_state(target)
    if after["up_mm"] < -LOWEST_TARGET_BELOW_GRASP_M * 1000.0:
        raise ScanRefused(f"this step targets the tool {after['up_mm']:+.1f} mm relative to the PROVEN grasp height: never below it")
    report = path_report(measured, target)
    if report["max_deviation_m"] > MAX_PATH_DEVIATION_M:
        raise ScanRefused(f"the joint-linear path leaves the straight line by {report['max_deviation_m'] * 1000:.1f} mm (limit {MAX_PATH_DEVIATION_M * 1000:.0f}): take a shorter step")
    if report["min_joint_limit_margin_deg"] < JOINT_LIMIT_MARGIN_DEG:
        raise ScanRefused(f"the path comes within {report['min_joint_limit_margin_deg']:.1f} deg of a joint limit")
    if report["min_true_singularity_distance_deg"] <= TRUE_SINGULARITY_MARGIN_DEG:
        raise ScanRefused(f"the path comes within {report['min_true_singularity_distance_deg']:.1f} deg of the straight-arm singularity")
    pose_id = f"step-fwd{forward_m * 1000:+.0f}-dz{dz_m * 1000:+.0f}-{'holding' if holding else 'open'}"
    return cli._pose(pose_id, "NEAR", target), report


GRASP_POSE_MAX_TOOL_TRAVEL_M = 0.030
GRASP_POSE_MAX_FORWARD_M = 0.060  # how far beyond the proven grasp range the grasp geometry may be shifted


def proven_grasp_pose(measured: dict, forward_m: float, dz_m: float):
    """FINAL APPROACH: the physically PROVEN grasp geometry itself (its joint triple, hence its tool orientation), shifted
    forward_m outward and dz_m up by the proven solver behind the proven gate, at the MEASURED pan. Unlike a relative step
    this restores the proven orientation after the realized poses have drifted from it. Bounded against the measured
    pose: the tool travels at most 30 mm, and no sample of the joint-linear path puts a link below the proven grasp
    tool height (the path, not only the endpoint)."""
    if not (0.0 <= dz_m <= STEP_MAX_DZ_M and 0.0 <= forward_m <= GRASP_POSE_MAX_FORWARD_M):
        raise ScanRefused(f"the grasp geometry may be shifted 0..{GRASP_POSE_MAX_FORWARD_M * 1000:.0f} mm outward and 0..{STEP_MAX_DZ_M * 1000:.0f} mm up, got {forward_m * 1000:+.1f} / {dz_m * 1000:+.1f}")
    target = {"shoulder_pan": float(measured["shoulder_pan"]), **cli._proven_grasp_shifted(forward_m, dz_m)}
    before, after = tool_state(measured), tool_state(target)
    travel = math.hypot(after["out_mm"] - before["out_mm"], after["up_mm"] - before["up_mm"]) / 1000.0
    if travel > GRASP_POSE_MAX_TOOL_TRAVEL_M:
        raise ScanRefused(f"the tool would travel {travel * 1000:.1f} mm to this grasp pose (limit {GRASP_POSE_MAX_TOOL_TRAVEL_M * 1000:.0f}): approach in bounded steps first")
    report = path_report(measured, target)
    if report["lowest_link_m"] < GRASP_TOOL_Z_M - LOWEST_TARGET_BELOW_GRASP_M:
        raise ScanRefused(f"the path to this grasp pose dips to {report['lowest_link_m']:+.3f} m, below the proven grasp tool height {GRASP_TOOL_Z_M:+.3f} m")
    return cli._pose(f"grasp-pose-fwd{forward_m * 1000:+.0f}-dz{dz_m * 1000:+.0f}", "NEAR", target), report


CARRY_MAX_TOOL_TRAVEL_M = 0.065
CARRY_MAX_FORWARD_M = 0.170


def proven_orientation_pose(measured: dict, forward_m: float, dz_m: float):
    """CARRY / PLACE target: the proven grasp geometry shifted forward_m outward and dz_m up, i.e. the PROVEN tool
    orientation at that point. Relative carry steps let the realized pitch drift toward vertical (91 deg on the first whole
    run), which wastes about 70 mm of reach; an absolute target restores the orientation on every step and its errors do
    not accumulate. Gate: the proven gate with the corrected elbow guard; the interpolated path is bounded as well."""
    if not (0.0 <= dz_m <= 0.060 and -0.030 <= forward_m <= CARRY_MAX_FORWARD_M):
        raise ScanRefused(f"carry target outside its bounds: forward {forward_m * 1000:+.0f} mm, up {dz_m * 1000:+.0f} mm")
    grasp = {**KNOWN_GOOD_GRASP_POSE_DEG, "wrist_roll": 0.0}
    solved = cartesian_lift.solve_cartesian_forward_and_z(grasp, forward_m, dz_m)
    accepted, reason = gate_step(solved, grasp, {"delta_forward_m": forward_m, "delta_z_m": dz_m})
    if not accepted:
        raise ScanRefused(f"the gate refuses this carry target: {reason}")
    if solved["orientation_error_deg"] > MAX_TILT_ERROR_DEG:
        raise ScanRefused(f"the proven orientation cannot be kept at this carry target (tilt error {solved['orientation_error_deg']:.2f} deg): reach limit")
    target = {"shoulder_pan": float(measured["shoulder_pan"]), **{k: round(float(solved["joint_deg"][k]), 3) for k in LIFT_JOINTS}}
    before, after = tool_state(measured), tool_state(target)
    travel = math.hypot(after["out_mm"] - before["out_mm"], after["up_mm"] - before["up_mm"]) / 1000.0
    if travel > CARRY_MAX_TOOL_TRAVEL_M:
        raise ScanRefused(f"the tool would travel {travel * 1000:.0f} mm in one carry step (limit {CARRY_MAX_TOOL_TRAVEL_M * 1000:.0f})")
    report = path_report(measured, target)
    if report["min_true_singularity_distance_deg"] <= TRUE_SINGULARITY_MARGIN_DEG or report["min_joint_limit_margin_deg"] < JOINT_LIMIT_MARGIN_DEG or report["lowest_link_m"] < CONTACT_FLOOR_M:
        raise ScanRefused(f"the interpolated carry path is not acceptable: {report}")
    return cli._pose(f"carry-fwd{forward_m * 1000:+.0f}-dz{dz_m * 1000:+.0f}", "NEAR", target), report


def pan_step_pose(measured: dict, delta_deg: float, holding: bool):
    if not 0.0 < abs(delta_deg) <= STEP_MAX_PAN_DEG:
        raise ScanRefused(f"a pan step must be non-zero and at most {STEP_MAX_PAN_DEG} deg, got {delta_deg}")
    state = tool_state(measured)
    if not holding and state["up_mm"] < PAN_NEEDS_TOOL_ABOVE_GRASP_M * 1000.0:
        raise ScanRefused(f"the tool is only {state['up_mm']:+.1f} mm above the grasp height: the open jaws may straddle the cube, a pan move would push it")
    target = {k: float(measured[k]) for k in SCAN_JOINTS}
    target["shoulder_pan"] = round(target["shoulder_pan"] + delta_deg, 3)
    return cli._pose(f"step-pan{delta_deg:+.2f}-{'holding' if holding else 'open'}", "NEAR", target)


def gripper_refusal(measured: dict, holding: bool):
    """None when the MEASURED gripper matches the declared phase (fails closed on an unreadable value)."""
    value = float(measured["gripper"])
    if holding:
        if not GRIPPER_HOLDING_BAND[0] <= value <= GRIPPER_HOLDING_BAND[1]:
            return f"the gripper measures {value:.2f}, outside the holding band {GRIPPER_HOLDING_BAND}"
    elif not abs(value - GRIPPER_OPEN) <= GRIPPER_OPEN_TOLERANCE:
        return f"the gripper measures {value:.2f}, not the proven normal-open {GRIPPER_OPEN} +/- {GRIPPER_OPEN_TOLERANCE}"
    return None


def closed_on_something(gripper_after_latch: float) -> bool:
    """Proprioceptive half of OBJECT_SECURED: an EMPTY close reaches the latch value; a cube stalls the jaw above it.
    Necessary, never sufficient."""
    return math.isfinite(gripper_after_latch) and gripper_after_latch - GRIPPER_LATCH > GRIPPER_EMPTY_LATCH_MARGIN and gripper_after_latch <= GRIPPER_HOLDING_BAND[1]


# ---------------------------------------------------------------------------------------------------------------------
# Perception-based decisions (pure functions). Image constants are for the DIRECT 1280x720 wrist frame and the present
# camera mount (rigid to the gripper); they were measured on the 2026-09-19 physical cycle and are recorded with their
# evidence in .local-dev/observations/manipulation-20260919/. They describe the camera/jaw geometry, not the scene.
# ---------------------------------------------------------------------------------------------------------------------
FOCAL_PX = 1200.0  # nominal; only ratios of it are used
CAMERA_TILT_BEYOND_TOOL_DEG = 43.3  # optical axis is this much steeper than the tool approach axis (per-view PnP, 42.8..46.1)
NADIR_COLUMN_PX = 516.0  # image column of "straight down" (the cube's left edge stayed at 505..508 while the cube grew on a vertical descent)
GRASP_REF_CENTER_PX = (540.0, 418.0)  # cube box centre in the proven-grasp frame (2026-09-18 reference, rigid-jaw mapping to the direct frame)
GRASP_REF_WIDTH_PX = 424.0  # 25.4 mm cube at grasp depth
CUBE_EDGE_MM = 25.4
CAPTURE_WINDOW_MM = (0.0, 9.0)  # cube centre, to the RIGHT of the reference: it rests against the fixed jaw, the moving jaw stands about 9 mm clear
LOW_POSE_REF_CONTACT_PX = (553.0, 545.0)  # cube contact pixel seen from the low scan pose for the cube that was grasped at LOW_POSE_REF_GRASP
LOW_POSE_REF_GRASP = {"pan_deg": -9.495, "out_mm": 11.4}
LOW_POSE_PX_PER_PAN_DEG = 11.8  # a more negative pan reading shifts the scene RIGHT by this many px per degree
LOW_POSE_PX_PER_RADIAL_MM = 2.7  # image-up = farther out
# The first close was EMPTY on every recorded run: the realized reach falls about 6 mm short of its command and the ALIGN radial
# estimate under-reads; the close that held came 8 to 17 mm farther out (2026-09-20: 21.0 -> 29.0, 19.0 -> about 36, 39.5 -> about 47).
# A short close is the harmful side: the jaw tips clip the near corner and spin the cube (11:36Z run, cube yawed about 40 deg);
# a deep one only seats the cube farther inside the 60 mm jaws, as in the proven reference grasp.
APPROACH_REACH_LEAD_MM = 14.0
PREGRASP_OUTWARD_BIAS_MM = 13.0  # a cube farther out than the tool clips at the top of the pregrasp frame; a nearer one stays whole
# THE NEAR BLOCK (T6 step 2a). Two arguments give the bound: coarse_pregrasp_from_low_pose clamps forward_mm to max(out + 13, 0),
# so a cube nearer than -13 mm has its pregrasp clamped and ALIGN can only ADVANCE (GRASP_RETRY_ADVANCE); and the jaws close
# APPROACH_REACH_LEAD_MM beyond the ALIGN estimate by design, so a block much nearer than the proven grasp radius is closed
# on short of its centre (run m8c5f9e9, 2026-09-26: cube_out -44.5, three empty closes, grasp_missed). A geometric argument
# from the clamp, not a measurement: the recorded points are the reference grasp at +11.4 and the miss at -44.5.
PREGRASP_MIN_CUBE_OUT_MM = -PREGRASP_OUTWARD_BIAS_MM
RETENTION_MIN_SCORE = 0.5  # held pairs scored 0.58..0.78, not-held pairs -0.20..0.41 on the 2026-09-19 frames (nine pairs: thin evidence)
RETENTION_BOX = (330, 250, 960, 690)
# FK-anchored depth inside the pad OVERSTATES the real one at long reach: 41 vs 32.5 mm measured (run 1), 29 vs 6.7 mm (first
# whole run, pitch drifted to 91 deg). The targets below are FK values and carry that derating; PLACED is decided by perception only.
PLACE_LEAD_MM = 8.0  # after a base advance the release radius is the grasp radius plus this: the loaded arm lags its target
CARRY_MIN_GAIN_MM = 5.0  # a carry step that gains less than this has reached the loaded reach limit: further steps only lose height
CARRY_HEIGHT_MM, PLACE_HEIGHT_MM = 30.0, 8.0
PLACED_MIN_EDGE_MARGIN_MM = CUBE_EDGE_MM * math.sqrt(2.0) / 2.0  # the whole FOOTPRINT inside the pad for any cube yaw: centre margin >= half-diagonal
RELEASE_MAX_HEIGHT_MM = 15.0  # measured tool height above the proven grasp height at the release; the proven releases were at about +3..+8, the pad is about 3 mm thick
PAD_SIZE_M = (0.415, 0.295)
LOADED_FORWARD_GAIN = 0.67  # a commanded forward step realizes about two thirds while carrying
# SEARCH SWEEP (the module docstring): the pan amplitude of the two sweep sectors, either side of the centre pan of the low pose.
# plan_scan_segments refuses an entry more than MAX_ENTRY_PAN_SWEEP_DEG (30) outside the pans of the call it plans, and the
# executor accepts an arrival up to ARRIVAL_TOLERANCE_DEG (5) past its target: a sector at the full 30 whose arrival overshoots
# by any amount could not plan its transit back through the centre (verified offline: 2 deg past a 30 deg sector is refused,
# past a 25 deg sector it plans). 25 is the widest sector every accepted arrival can return from; no sector leaves +/-30.
SEARCH_SWEEP_DEG = MAX_ENTRY_PAN_SWEEP_DEG - ARRIVAL_TOLERANCE_DEG
# RETURN BOUND (the module docstring, SEARCH SWEEP): EXIT and the HOLD_OBSERVE rise of a base advance plan ONE pose at the
# saved low pose's pan from wherever the arm is, and the same entry bound refuses that call from a pan more than
# MAX_ENTRY_PAN_SWEEP_DEG away. A pan COMMANDED within this of the saved pan measures within the entry bound at every
# arrival the executor accepts, so the return can always plan; the coarse pregrasp pan and every ALIGN trim are held to it
# before they are commanded, and nothing is picked up that could not be returned from.
RETURN_PAN_BOUND_DEG = MAX_ENTRY_PAN_SWEEP_DEG - ARRIVAL_TOLERANCE_DEG


def coarse_pregrasp_from_low_pose(contact_px, pan_now_deg: float) -> dict:
    """COARSE geometry from ONE low-scan-pose frame: which pan reading and which forward offset (relative to the proven
    grasp) put the pregrasp over the cube. Anchored on the physically completed grasp; it only has to land the cube
    inside the pregrasp view (about 100 x 190 mm of floor), the wrist loop does the rest."""
    pan = pan_now_deg + (float(contact_px[0]) - LOW_POSE_REF_CONTACT_PX[0]) / LOW_POSE_PX_PER_PAN_DEG
    pan += LOW_POSE_REF_GRASP["pan_deg"] - (-9.582)  # the reference frame was taken at reading -9.582
    out = LOW_POSE_REF_GRASP["out_mm"] + (LOW_POSE_REF_CONTACT_PX[1] - float(contact_px[1])) / LOW_POSE_PX_PER_RADIAL_MM
    return {"pan_deg": round(pan, 2), "forward_mm": round(min(max(out + PREGRASP_OUTWARD_BIAS_MM, 0.0), 60.0), 1), "cube_out_mm": round(out, 1)}


def nadir_row_px(pitch_deg: float) -> float:
    return 360.0 - FOCAL_PX * math.tan(math.radians(pitch_deg + CAMERA_TILT_BEYOND_TOOL_DEG - 90.0))


def alignment_from_bbox(bbox_xyxy, pitch_deg: float) -> dict:
    """Propagate the detected cube to GRASP DEPTH (image expansion about the nadir as the camera descends) and compare
    it with the proven-grasp reference. lateral_mm > 0: the cube would end to the RIGHT of the reference position;
    radial_mm > 0: FARTHER out than it. ALIGNED when the lateral value lies inside the jaw capture window."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    scale = GRASP_REF_WIDTH_PX / max(x1 - x0, 1.0)
    center_x = NADIR_COLUMN_PX + scale * ((x0 + x1) / 2.0 - NADIR_COLUMN_PX)
    row = nadir_row_px(pitch_deg)
    center_y = row + scale * ((y0 + y1) / 2.0 - row)
    px_per_mm = GRASP_REF_WIDTH_PX / CUBE_EDGE_MM
    lateral = (center_x - GRASP_REF_CENTER_PX[0]) / px_per_mm
    radial = (GRASP_REF_CENTER_PX[1] - center_y) / px_per_mm
    return {"lateral_mm": lateral, "radial_mm": radial, "scale_to_grasp": scale, "aligned": CAPTURE_WINDOW_MM[0] <= lateral <= CAPTURE_WINDOW_MM[1]}


def lateral_trim_deg(lateral_mm: float, cube_range_m: float) -> float:
    """Pan READING delta that brings the cube to the middle of the capture window (+ = camera turns right, scene shifts
    left). A commanded pan step realizes about 0.5 deg short, so that much is added in the direction of the move."""
    wanted_mm = lateral_mm - sum(CAPTURE_WINDOW_MM) / 2.0
    delta = math.degrees(math.atan2(wanted_mm / 1000.0, cube_range_m))
    delta += math.copysign(0.5, delta) if abs(delta) > 0.2 else 0.0
    return max(-STEP_MAX_PAN_DEG, min(STEP_MAX_PAN_DEG, round(delta, 2)))


def retention_score(frame_a, frame_b) -> float:
    """Visual half of OBJECT_SECURED, colour-free: a HELD cube is rigid with the camera, so the image between the jaws is
    the same in two frames taken at different heights; anything left on the floor moves and rescales. Mean of the
    normalized cross-correlations of the smoothed grey patch and of its x / y gradients."""
    import cv2

    def patch(frame):
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        x0, y0, x1, y1 = RETENTION_BOX
        return cv2.resize(cv2.GaussianBlur(grey[y0:y1, x0:x1], (0, 0), 3), (126, 88), interpolation=cv2.INTER_AREA).astype(np.float32)

    def ncc(a, b):
        a, b = a - a.mean(), b - b.mean()
        return float((a * b).sum() / math.sqrt(float((a * a).sum()) * float((b * b).sum()) + 1e-9))

    a, b = patch(frame_a), patch(frame_b)
    gradients = [ncc(cv2.Sobel(a, cv2.CV_32F, dx, dy, ksize=3), cv2.Sobel(b, cv2.CV_32F, dx, dy, ksize=3)) for dx, dy in ((1, 0), (0, 1))]
    return (ncc(a, b) + sum(gradients) / 2.0) / 2.0


def distance_outside_pad_mm(cube_center_pad_m) -> float:
    """How far OUTSIDE the pad rectangle the cube centre lies (0 when inside): the pickup-to-edge distance of the carry."""
    dx = max(abs(float(cube_center_pad_m[0])) - PAD_SIZE_M[0] / 2.0, 0.0)
    dy = max(abs(float(cube_center_pad_m[1])) - PAD_SIZE_M[1] / 2.0, 0.0)
    return math.hypot(dx, dy) * 1000.0


def placed_margin_mm(cube_center_pad_m) -> float:
    """Distance from the cube centre to the nearest pad edge, negative outside. PLACED needs at least the cube's
    half-diagonal, so that the whole footprint is inside whatever the cube's yaw."""
    return min(PAD_SIZE_M[0] / 2.0 - abs(float(cube_center_pad_m[0])), PAD_SIZE_M[1] / 2.0 - abs(float(cube_center_pad_m[1]))) * 1000.0


# ---------------------------------------------------------------------------------------------------------------------
# RELEASE ADMISSION from pad-relative visual evidence. The ANCHOR is the pad point under the tool at the grasp
# configuration: the cube centre the frozen perception measured in PAD_FRAME at SEARCH. A base advance moves it, and that
# move is MEASURED between two pad frames taken with the arm held, never assumed. FK only contributes the short travel
# from the grasp radius to the release radius, and only for what it physically realized on the worst recorded run.
# PAD_FRAME is not obtainable at carry height (the frame shows one logo row, which does not fix the plane), so the pad is
# observed from the low scan pose, also while the cube is held.
# ---------------------------------------------------------------------------------------------------------------------
PLACE_CLEARANCE_TARGET_MM = 20.0  # wanted around the cube FOOTPRINT, whatever its yaw
PLACE_REQUIRED_CENTRE_MM = PLACED_MIN_EDGE_MARGIN_MM + PLACE_CLEARANCE_TARGET_MM
VISUAL_ANCHOR_UNCERTAINTY_MM = 6.0  # PAD_FRAME near the hem (a few mm; hiding the whole lower half of a low-pose frame moved it 1.6 mm) plus the cube shifting as the jaws close
FK_TRAVEL_OVERSTATEMENT = 1.6  # loaded carry, FK travel / travel measured by perception: 65.8 / 42.0 and 59.5 / 55.0 on the two 2026-09-20 runs (thin evidence)
ARM_LOADED_REACH_OUT_MM = 95.0  # the loaded carry stopped gaining at +94.8 and +112 mm out
BASE_VX_MPS = 0.05  # the commanded speed of the in-process forward primitive run on hardware (physical pass 2026-09-18); the pad measured about twice it (BASE_RATE_PRIOR_MM_S); qualifies nothing about the capability relay
BASE_PULSE_S = (0.2, 0.8)  # durations the in-process primitive ran on hardware: 0.2 s (about 12 mm loaded), 0.5 s (about 50 mm), 1.5 s
BASE_RATE_PRIOR_MM_S = 100.0  # 50.8 mm in 0.5 s; the first pulse covers at most half the need, then the MEASURED rate is used
BASE_RATE_BOUNDS_MM_S = (40.0, 200.0)
BASE_MAX_PULSES = 4
BASE_MAX_TOTAL_ADVANCE_MM = 120.0
BASE_TARGET_BUFFER_MM = 15.0  # the advance aims this far beyond the admission minimum, so a lagging arm (negative travel) is still admitted
BASE_ADVANCE_TOLERANCE_MM = 8.0
BASE_MAX_LATERAL_MM = 8.0  # plus BASE_MAX_AXIS_SLOPE x the advance: the base drives along ITS x axis, the camera looks along the arm plane (pan about -9 deg)
BASE_MAX_AXIS_SLOPE = 0.45  # about 24 deg between the measured displacement and the expected axis
BASE_PLAUSIBLE_SLACK_MM = 10.0  # a pulse cannot have moved the base farther than the fastest bounded rate x its duration plus this
BASE_MAX_DISTORTION_MM = 4.0  # two pad frames must agree on the shape of a 50 mm cross around the anchor, or they do not measure a displacement
BASE_MAX_ARM_DRIFT_DEG = 0.6  # "the arm is held during translation" is measured, not assumed (recorded noise: up to 0.4 deg); 0.6 deg is about 5 mm on the pad
BASE_POSTURE_TOLERANCE_DEG = 6.0  # the base only moves with the arm AT the saved low scan pose; its elbow arrives 2.5 to 3.1 deg off and stood 3.8 deg off after a settle (2026-09-20)
BASE_RETURN_TOLERANCE_MM = 10.0
# THE PLACEMENT ENVELOPE (docs/autonomous-demo/one-block-demo-2026-09-26.md, T6). KC_PLACEMENT_ENVELOPE names the envelope
# words of a run, read ONCE in _run beside KC_RUNTIME_MODE into PLACEMENT_ENVELOPE. Absent, every decision below is byte for
# byte the proven one. Each word opens one step of T6 and is refused in a LIVE run until PLACEMENT_ENVELOPE_QUALIFIED names it,
# which a test allows only when a docs/autonomous-demo/placement-envelope-qualification-*.md names the word with its rows;
# the one way past that gate is the attended label the operator's config writes for the qualification runs themselves.
# The key carries EVERY stage's words (docs/autonomous-demo/overnight-plan-2026-09-26.md, section 2.6; stage_policy, THE
# WORDS): each stage policy module declares its words and its qualified ones, a word no stage declares is refused, and a
# LIVE run is refused a word no stage has qualified (envelope_words_qualified) without the attended label. The placement
# words keep the names and the rules above; PLACEMENT_ENVELOPE holds the run's words of every stage.
PLACEMENT_ENVELOPE_ENV, PLACEMENT_ENVELOPE_ATTENDED_ENV = "KC_PLACEMENT_ENVELOPE", "KC_PLACEMENT_ENVELOPE_ATTENDED"  # demo_runtime.supervisor exports both; a test keeps them equal
PLACEMENT_ENVELOPE_WORDS = policy_placement.WORDS  # T6 steps 1b, 2b, 3a, 3b, declared by the placement stage; every later word needs 'projected' as well
PLACEMENT_ENVELOPE_QUALIFIED = policy_placement.QUALIFIED  # a word goes in there in the same commit as its qualification record, never before
PLACEMENT_ENVELOPE = ()  # this run's words, set by _run; () is the proven envelope
PLACEMENT_ENVELOPE_ATTENDED = None  # the attended label of a qualification run, stamped into the record; None otherwise
# T6 step 3a (side_offset): the arm releases to the side of the reach axis AFTER the advance, by a pan of the loaded arm
# about the pan axis. Model: pivot P = anchor - r u, release point R(delta) = P + r rot(u, s delta), s the sign of the proven
# lateral convention (lateral_trim_deg: a POSITIVE pan reading turns the tool RIGHT; a positive rotation of u is to the
# left, so s = -1). The cap, the uncertainty and the realization are the STARTING values the calibration session fixes
# (the forced-offset runs of the qualification, KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG, refused in LIVE without the attended
# label): the loaded pan itself has a record (run 2 of 2026-09-26, 20.5 deg from home, held), the offset's realization none.
PLACE_SIDE_PAN_MAX_DEG = 15.0  # 25 after three more starts
SIDE_OFFSET_UNCERTAINTY_MM = 4.0  # the 0.5 deg trim shortfall of lateral_trim_deg is 1.9 mm at 0.22 m, plus 2
SIDE_OFFSET_REALIZATION = 1.0  # realized chord / modelled chord, derived from PLACED against the prediction the way FK_TRAVEL_OVERSTATEMENT was
SIDE_OFFSET_PAN_SIGN = -1.0
PLACEMENT_SIDE_OFFSET_FORCE_ENV = "KC_PLACEMENT_SIDE_OFFSET_FORCE_DEG"
PLACEMENT_SIDE_OFFSET_FORCE_DEG = None  # the qualification-only forced offset of this run, set by _run; None otherwise
# T6 step 3b (base_lateral): the lateral pulse of the holonomic base, its constants and its measurement only in this round
# (the planner is autonomy/manipulation/holonomic_placement.py). No lateral primitive is wired into the Cycle: the command
# path (the executor's gateway-direct closure, or a contract v3 through the capability) needs a ruling first.
LATERAL_VY_MPS = 0.05  # the magnitude the gateway clamp proved for +y.vel on 2026-09-15 (about 1 cm, unloaded); the realized rate is UNKNOWN until a pulse measures it
LATERAL_PULSE_S = BASE_PULSE_S
LATERAL_RATE_PRIOR_MM_S = 50.0  # the forward pulses realized 49 to 57 mm/s at 0.05 m/s; the first pulse covers at most half the need
LATERAL_MAX_PULSES = 4
LATERAL_MAX_TOTAL_MM = 120.0
LATERAL_TOLERANCE_MM = 8.0
LATERAL_MAX_ALONG_MM = 8.0  # plus LATERAL_MAX_AXIS_SLOPE x the lateral: BASE_MAX_LATERAL_MM and BASE_MAX_AXIS_SLOPE transposed
LATERAL_MAX_AXIS_SLOPE = 0.45
# The projected rule ('projected'): the 15 mm buffer is the lagging arm, which acts along the reach direction only, so it is
# projected onto each edge normal; the 6 mm anchor uncertainty is a 2D position error and stays on every edge; and the two
# PLAN-TIME callers charge this allowance on the edges the reach axis runs along, for the sideways drift of the advance that
# the loop measures and the plan cannot (recorded totals over one advance: 3.8 and 5.7 mm). The loop asks no allowance.
PLACE_SIDE_DRIFT_ALLOWANCE_MM = 6.0
PAD_EDGE_NORMALS = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))  # right, left, far, near: the order of every four-margin tuple below


def _word_policies() -> tuple:
    """The five stage policies and the additive ones (_ADDITIVE_POLICIES), read when asked: every policy that declares a word."""
    return _STAGE_POLICIES + _ADDITIVE_POLICIES


def envelope_words_declared() -> tuple:
    """Every word a stage policy or an additive policy of this controller declares (stage_policy, THE WORDS), in stage order,
    the additive policies' last."""
    return stage_policy.declared_words(_word_policies())


def envelope_words_qualified() -> frozenset:
    """Every word a stage policy or an additive policy has qualified: a LIVE run may name these without the attended label."""
    return stage_policy.qualified_words(_word_policies())


def placement_envelope_words(environ=None) -> tuple:
    """The words KC_PLACEMENT_ENVELOPE names for every stage, in order, or () when it is absent or empty. A word no stage
    declares, a repeated word, or a later placement word without 'projected' is a ScanRefused: the refusal names no value."""
    environ = os.environ if environ is None else environ
    words = tuple(word.strip() for word in (environ.get(PLACEMENT_ENVELOPE_ENV) or "").split(",") if word.strip())
    refusal = stage_policy.envelope_refusal(words, _word_policies(), PLACEMENT_ENVELOPE_WORDS)
    if refusal == "unknown":
        raise ScanRefused(f"{PLACEMENT_ENVELOPE_ENV} names an envelope word no stage of this controller declares; the words it knows are {', '.join(envelope_words_declared())}")
    if refusal is not None:
        raise ScanRefused(f"{PLACEMENT_ENVELOPE_ENV} names a later placement envelope word without projected, which every later placement word builds on")
    return words


def set_placement_envelope(words: tuple, attended=None, side_offset_force_deg=None) -> None:
    global PLACEMENT_ENVELOPE, PLACEMENT_ENVELOPE_ATTENDED, PLACEMENT_SIDE_OFFSET_FORCE_DEG
    PLACEMENT_ENVELOPE, PLACEMENT_ENVELOPE_ATTENDED, PLACEMENT_SIDE_OFFSET_FORCE_DEG = tuple(words), attended, side_offset_force_deg


def placement_side_offset_force_deg(environ=None):
    """The qualification-only forced release offset (PLACEMENT_SIDE_OFFSET_FORCE_ENV), in degrees of pan, or None when it is
    absent or empty. It needs the word side_offset among the envelope words and stays inside the cap; anything else is a
    ScanRefused that names no value."""
    environ = os.environ if environ is None else environ
    text = (environ.get(PLACEMENT_SIDE_OFFSET_FORCE_ENV) or "").strip()
    if not text:
        return None
    if "side_offset" not in placement_envelope_words(environ):
        raise ScanRefused(f"{PLACEMENT_SIDE_OFFSET_FORCE_ENV} is set and {PLACEMENT_ENVELOPE_ENV} does not name side_offset: the forced offset belongs to that word's qualification only")
    try:
        force = float(text)
    except ValueError:
        raise ScanRefused(f"{PLACEMENT_SIDE_OFFSET_FORCE_ENV} is not a number of degrees") from None
    if not math.isfinite(force) or abs(force) > PLACE_SIDE_PAN_MAX_DEG:
        raise ScanRefused(f"{PLACEMENT_SIDE_OFFSET_FORCE_ENV} lies outside the cap of {PLACE_SIDE_PAN_MAX_DEG:.0f} deg")
    return force


def _rotated(vector, deg: float) -> tuple:
    c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (c * float(vector[0]) - s_ * float(vector[1]), s_ * float(vector[0]) + c * float(vector[1]))


def offset_release_point(anchor_pad_m, direction, r_release_mm: float, delta_deg: float, frame_to_grasp_deg: float = 0.0) -> dict:
    """Where a pan of delta_deg (a pan READING delta, + = the tool turns right) moves the release point: the anchor rotated
    about the pan axis, which lies r_release_mm behind it along the tool direction u (the reach direction of the SEARCH
    frame turned by the pan the grasp added to the frame's). The chord r sin(delta) is sideways, the shrink r (1 - cos delta)
    is along the axis, both in the model and both signed like delta."""
    u = _rotated(direction, SIDE_OFFSET_PAN_SIGN * frame_to_grasp_deg)
    pivot = (float(anchor_pad_m[0]) - r_release_mm * u[0] / 1000.0, float(anchor_pad_m[1]) - r_release_mm * u[1] / 1000.0)
    u_delta = _rotated(u, SIDE_OFFSET_PAN_SIGN * delta_deg)
    point = [pivot[0] + r_release_mm * u_delta[0] / 1000.0, pivot[1] + r_release_mm * u_delta[1] / 1000.0]
    return {"point_pad_m": point, "direction": u_delta, "pivot_pad_m": [pivot[0], pivot[1]],
            "chord_mm": r_release_mm * math.sin(math.radians(delta_deg)), "shrink_mm": r_release_mm * (1.0 - math.cos(math.radians(delta_deg)))}


def release_offset_plan(anchor_pad_m, direction, grasp_pan_deg: float, frame_pan_deg: float, home_pan_deg: float, r_release_mm: float, side_allowance_mm: float = 0.0, fk_travel_mm: float = 0.0):
    """The smallest |delta| (0.5 deg grid, 0 first) whose release point meets the PROJECTED rule at both ends of the FK
    travel, less SIDE_OFFSET_UNCERTAINTY_MM for any non-zero delta, inside PLACE_SIDE_PAN_MAX_DEG and inside the return
    bound (|grasp pan + delta - home pan| <= RETURN_PAN_BOUND_DEG). None when no such delta exists. delta 0 whenever the
    rule already admits the anchor itself: the two recorded runs plan 0 and are unchanged."""
    frame_to_grasp = grasp_pan_deg - frame_pan_deg
    steps = int(round(PLACE_SIDE_PAN_MAX_DEG / 0.5))
    candidates = [0.0] + [sign * k * 0.5 for k in range(1, steps + 1) for sign in (-1.0, 1.0)]
    for delta in candidates:
        if abs(grasp_pan_deg + delta - home_pan_deg) > RETURN_PAN_BOUND_DEG:
            continue
        geometry = offset_release_point(anchor_pad_m, direction, r_release_mm, delta, frame_to_grasp)
        required = required_edge_margins_mm(geometry["direction"], side_allowance_mm, projected=True)
        extra = SIDE_OFFSET_UNCERTAINTY_MM if delta else 0.0
        ends = (fk_travel_mm, fk_travel_mm / FK_TRAVEL_OVERSTATEMENT)
        points = [(geometry["point_pad_m"][0] + geometry["direction"][0] * t / 1000.0, geometry["point_pad_m"][1] + geometry["direction"][1] * t / 1000.0) for t in ends]
        if all(margin - extra >= need for point in points for margin, need in zip(edge_margins_mm(point), required)):
            return {"delta_deg": delta, "release_point_pad_m": geometry["point_pad_m"], "release_direction": geometry["direction"], "chord_mm": geometry["chord_mm"],
                    "shrink_mm": geometry["shrink_mm"], "pan_from_home_deg": abs(grasp_pan_deg + delta - home_pan_deg), "r_release_mm": r_release_mm}
    return None


def envelope_projected(projected=None) -> bool:
    return ("projected" in PLACEMENT_ENVELOPE) if projected is None else bool(projected)


def _through(homography, point) -> np.ndarray:
    vector = np.asarray(homography, dtype=float) @ np.array([float(point[0]), float(point[1]), 1.0])
    return vector[:2] / vector[2]


def reach_direction_pad(image_to_pad, pixel) -> tuple:
    """Unit vector, in PAD_FRAME, of 'farther out along the arm' at a low-pose pixel: image-up (the camera looks along the arm plane)."""
    step = _through(image_to_pad, (pixel[0], pixel[1] - 40.0)) - _through(image_to_pad, pixel)
    return tuple(float(v) for v in step / np.linalg.norm(step))


def release_margin_worst_mm(anchor_pad_m, direction, fk_travel_mm: float) -> float:
    """LOWER bound of the released cube centre's distance to the nearest pad edge. The true travel from the anchor lies
    between fk_travel / FK_TRAVEL_OVERSTATEMENT and fk_travel (a negative travel, a lagging arm, counts in full); the margin
    is concave along that segment, so its minimum is at an end; the anchor's own uncertainty comes off on top."""
    ends = (fk_travel_mm, fk_travel_mm / FK_TRAVEL_OVERSTATEMENT)
    points = [(float(anchor_pad_m[0]) + direction[0] * t / 1000.0, float(anchor_pad_m[1]) + direction[1] * t / 1000.0) for t in ends]
    return min(placed_margin_mm(point) for point in points) - VISUAL_ANCHOR_UNCERTAINTY_MM


def release_segment_pad(anchor_pad_m, direction, fk_travel_mm: float) -> tuple:
    """The two PAD_FRAME ends of the segment release_margin_worst_mm puts the released cube centre on: the anchor moved by
    the FK travel and by the travel / FK_TRAVEL_OVERSTATEMENT along the reach direction. What a mission's PLACED compares
    the EXIT view's blocks with (target_binding, THE RELEASE AGREEMENT). Nothing moves on it."""
    return tuple((float(anchor_pad_m[0]) + direction[0] * t / 1000.0, float(anchor_pad_m[1]) + direction[1] * t / 1000.0)
                 for t in (fk_travel_mm, fk_travel_mm / FK_TRAVEL_OVERSTATEMENT))


def placement_plan(anchor_pad_m, direction, grasp_out_mm: float, side_allowance_mm: float = 0.0) -> dict:
    """ARM_ONLY where the loaded reach achieves the required margin (the smallest such travel plus 5 mm); otherwise the base
    advance that brings the anchor itself to the required margin plus the buffer, so the release happens at about the
    grasp radius and FK barely matters. side_allowance_mm is the plan-time side drift allowance of the projected rule
    (PLACE_SIDE_DRIFT_ALLOWANCE_MM at the two plan-time callers), charged only under the word."""
    for travel in np.arange(0.0, max(ARM_LOADED_REACH_OUT_MM - grasp_out_mm, 0.0) + 0.5, 1.0):
        if release_margin_worst_mm(anchor_pad_m, direction, float(travel)) >= PLACE_REQUIRED_CENTRE_MM:
            return {"mode": "ARM_ONLY", "fk_travel_mm": float(min(travel + 5.0, max(ARM_LOADED_REACH_OUT_MM - grasp_out_mm, travel))), "base_advance_mm": 0.0}
    advance = base_advance_needed_mm(anchor_pad_m, direction, side_allowance_mm)
    if advance is None:
        # A pure function, so it carries no reason code of its own: BOTH callers (the pre-pickup feasibility check at
        # SEARCH and its post-grasp twin) catch this and re-raise with the code and the values of their own moment.
        raise CycleFailed(f"no bounded base advance (at most {BASE_MAX_TOTAL_ADVANCE_MM:.0f} mm) brings the grasp point {PLACE_REQUIRED_CENTRE_MM:.0f} mm inside the pad from here")
    return {"mode": "BASE_ADVANCE", "fk_travel_mm": 0.0, "base_advance_mm": advance}


def edge_margins_mm(point_pad_m) -> tuple:
    """The raw distance from a pad point to each of the four edges (right, left, far, near: PAD_EDGE_NORMALS), negative
    outside. Their minimum is placed_margin_mm."""
    x, y = float(point_pad_m[0]) * 1000.0, float(point_pad_m[1]) * 1000.0
    half_x, half_y = PAD_SIZE_M[0] * 500.0, PAD_SIZE_M[1] * 500.0
    return (half_x - x, half_x + x, half_y - y, half_y + y)


def _side_edges(direction) -> tuple:
    """True for each edge the reach axis runs ALONG (the left normal projects on it at least as much as the direction does).
    An exactly diagonal axis marks every edge (placement_envelope.side_edges, the same rule), so the side edges are never empty."""
    left = (-float(direction[1]), float(direction[0]))
    return tuple(abs(left[0] * e[0] + left[1] * e[1]) >= abs(float(direction[0]) * e[0] + float(direction[1]) * e[1]) for e in PAD_EDGE_NORMALS)


def required_edge_margins_mm(direction, side_allowance_mm: float = 0.0, projected=None) -> tuple:
    """What the moved anchor's raw margin must reach on each edge (right, left, far, near) for a base advance to be planned.
    The proven rule: the required centre margin plus the buffer plus the anchor uncertainty on EVERY edge (58.96). The
    projected rule (envelope word 'projected'): the buffer times |d . e| per edge, the allowance on the side edges only."""
    if not envelope_projected(projected):
        return tuple(PLACE_REQUIRED_CENTRE_MM + BASE_TARGET_BUFFER_MM + VISUAL_ANCHOR_UNCERTAINTY_MM for _ in PAD_EDGE_NORMALS)
    sides = _side_edges(direction)
    return tuple(PLACE_REQUIRED_CENTRE_MM + VISUAL_ANCHOR_UNCERTAINTY_MM + BASE_TARGET_BUFFER_MM * abs(float(direction[0]) * e[0] + float(direction[1]) * e[1]) + (float(side_allowance_mm) if side else 0.0)
                 for e, side in zip(PAD_EDGE_NORMALS, sides))


def side_margin_mm(anchor_pad_m, direction) -> float:
    """The anchor's smallest raw margin to the edges the reach axis runs along: the one an advance along the axis cannot grow."""
    return min(margin for margin, side in zip(edge_margins_mm(anchor_pad_m), _side_edges(direction)) if side)


def axis_advance_needed_unbounded_mm(anchor_pad_m, direction, side_allowance_mm: float = 0.0):
    """The smallest advance along the axis after which the edges the axis CROSSES meet their requirement, the side edges
    ignored and the bound ignored: what a refusal states so the operator's sentence names the along-axis shortfall itself.
    None when no advance along this axis crosses into the requirement (an axis parallel to the pad)."""
    required, sides = required_edge_margins_mm(direction, side_allowance_mm), _side_edges(direction)
    margins = edge_margins_mm(anchor_pad_m)
    needs = []
    for margin, need, e, side in zip(margins, required, PAD_EDGE_NORMALS, sides):
        rate = -(float(direction[0]) * e[0] + float(direction[1]) * e[1])  # mm of margin gained on this edge per mm of advance
        if side or rate <= 1e-9:
            continue
        needs.append(max(need - margin, 0.0) / rate)
    return max(needs) if needs else None


def placement_refusal_values(anchor_pad_m, direction, side_allowance_mm: float = 0.0) -> dict:
    """The values a placement refusal carries beside the sentence: the side margin against its requirement, the along-axis
    requirement, and the along-axis need with the bound ignored, so that a start-3-class block reads as '1.6 mm past the
    along-axis limit' and never as 'the side rule would have fixed it'."""
    required, sides = required_edge_margins_mm(direction, side_allowance_mm), _side_edges(direction)
    along = [need for need, side in zip(required, sides) if not side]
    return {"side_margin_mm": side_margin_mm(anchor_pad_m, direction), "required_side_margin_mm": max(need for need, side in zip(required, sides) if side),
            "required_axis_margin_mm": max(along) if along else None, "axis_advance_needed_unbounded_mm": axis_advance_needed_unbounded_mm(anchor_pad_m, direction, side_allowance_mm)}


def base_advance_needed_mm(anchor_pad_m, direction, side_allowance_mm: float = 0.0):
    """Smallest advance along the reach direction after which the anchor alone meets the requirement plus the buffer; None when
    the bounded total does not cover it. THE RULE of T6 step 1b lives here and nowhere else: under the proven envelope the
    test is the one every recorded run was decided by, spelled exactly as it was; under 'projected' it is the per-edge one."""
    if not envelope_projected():
        for advance in np.arange(0.0, BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
            moved = (float(anchor_pad_m[0]) + direction[0] * advance / 1000.0, float(anchor_pad_m[1]) + direction[1] * advance / 1000.0)
            if release_margin_worst_mm(moved, direction, 0.0) >= PLACE_REQUIRED_CENTRE_MM + BASE_TARGET_BUFFER_MM:
                return float(advance)
        return None
    required = required_edge_margins_mm(direction, side_allowance_mm)
    for advance in np.arange(0.0, BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0):
        moved = (float(anchor_pad_m[0]) + direction[0] * advance / 1000.0, float(anchor_pad_m[1]) + direction[1] * advance / 1000.0)
        if all(margin >= need for margin, need in zip(edge_margins_mm(moved), required)):
            return float(advance)
    return None


def base_displacement(anchor_pad_m, direction, pad_to_image_before, image_to_pad_after) -> dict:
    """Where a floor point fixed to the ROBOT went in PAD_FRAME, from two pad frames taken with the arm HELD: the pixel that
    saw the anchor before the move sees the anchor's new place after it. The same is done for a 50 mm cross around it; the
    two frames only measure a displacement if that cross keeps its shape."""
    def moved(point):
        return _through(image_to_pad_after, _through(pad_to_image_before, point))

    centre = np.array([float(anchor_pad_m[0]), float(anchor_pad_m[1])])
    after = moved(centre)
    arms = [np.array(offset) for offset in ((0.05, 0.0), (-0.05, 0.0), (0.0, 0.05), (0.0, -0.05))]
    distortion = max(abs(float(np.linalg.norm(moved(centre + arm) - after)) - 0.05) for arm in arms) * 1000.0
    shift = (after - centre) * 1000.0
    along = float(shift @ np.array(direction))
    return {"anchor_pad_m": [float(after[0]), float(after[1])], "advance_mm": along, "lateral_mm": float(direction[0] * shift[1] - direction[1] * shift[0]), "distortion_mm": distortion,
            "shift_pad_mm": [float(shift[0]), float(shift[1])]}


def holonomic_rule(direction, side_allowance_mm: float = 0.0) -> tuple:
    """The per-edge rule the holonomic planner (autonomy/manipulation/holonomic_placement.py) is handed: this executor's
    required_edge_margins_mm under this run's envelope word, so the planner and the executor never disagree on a number."""
    return tuple(required_edge_margins_mm(direction, side_allowance_mm))


def lateral_displacement(reference_pad_m, direction, pad_to_image_before, image_to_pad_after) -> dict:
    """T6 step 3b: where a floor point fixed to the ROBOT went in PAD_FRAME across a LATERAL pulse, from two pad frames taken
    with the arm held, on the same 50 mm cross as base_displacement, under honest names: aside_mm along the LEFT normal of
    the reach direction (the sign of base_displacement's lateral_mm), along_mm along the direction. Never advance_mm: a
    lateral move is not an advance in the record."""
    def moved(point):
        return _through(image_to_pad_after, _through(pad_to_image_before, point))

    centre = np.array([float(reference_pad_m[0]), float(reference_pad_m[1])])
    after = moved(centre)
    arms = [np.array(offset) for offset in ((0.05, 0.0), (-0.05, 0.0), (0.0, 0.05), (0.0, -0.05))]
    distortion = max(abs(float(np.linalg.norm(moved(centre + arm) - after)) - 0.05) for arm in arms) * 1000.0
    shift = (after - centre) * 1000.0
    return {"anchor_pad_m": [float(after[0]), float(after[1])], "aside_mm": float(direction[0] * shift[1] - direction[1] * shift[0]), "along_mm": float(shift @ np.array(direction)),
            "distortion_mm": distortion, "shift_pad_mm": [float(shift[0]), float(shift[1])]}


def lateral_measurement_refusal(moved, commanded_sign: float, duration_s: float):
    """None when two pad frames measure a plausible bounded LATERAL translation for a pulse of duration_s commanded in the
    direction of commanded_sign (+ = left); otherwise the sentence. The forward gates transposed: the cross keeps its shape,
    the move along the reach axis stays within LATERAL_MAX_ALONG_MM plus the slope times the lateral, the signed lateral is
    not against the command beyond 5 mm and not faster than the fastest bounded rate plus the slack. No pad frame is a refusal."""
    if moved is None:
        return "PAD_FRAME is not valid after the lateral pulse: the displacement is unknown"
    signed = float(moved["aside_mm"]) * math.copysign(1.0, commanded_sign)
    if float(moved["distortion_mm"]) > BASE_MAX_DISTORTION_MM:
        return f"the two pad frames disagree on the shape of the 50 mm cross by {moved['distortion_mm']:.1f} mm (at most {BASE_MAX_DISTORTION_MM})"
    if abs(float(moved["along_mm"])) > LATERAL_MAX_ALONG_MM + LATERAL_MAX_AXIS_SLOPE * abs(float(moved["aside_mm"])):
        return f"the base moved {moved['along_mm']:.1f} mm along the reach axis during a lateral pulse (at most {LATERAL_MAX_ALONG_MM} plus {LATERAL_MAX_AXIS_SLOPE} x the lateral)"
    if signed < -5.0:
        return f"the base moved {abs(signed):.1f} mm against the commanded lateral direction (at most 5)"
    if signed > BASE_RATE_BOUNDS_MM_S[1] * duration_s + BASE_PLAUSIBLE_SLACK_MM:
        return f"the base cannot have moved {signed:.1f} mm sideways in {duration_s:.2f} s (at most {BASE_RATE_BOUNDS_MM_S[1]:.0f} mm/s plus {BASE_PLAUSIBLE_SLACK_MM:.0f} mm)"
    return None


def base_posture_refusal(measured: dict, home: dict):
    """None when every arm joint MEASURES within the tolerance of the saved low scan pose (fails closed on an unreadable value)."""
    off = {k: float(measured.get(k, float("nan"))) - float(home[k]) for k in SCAN_JOINTS}
    bad = {k: round(v, 2) for k, v in off.items() if not abs(v) <= BASE_POSTURE_TOLERANCE_DEG}
    return f"the arm is not at the saved low scan pose (off by {bad} deg, tolerance {BASE_POSTURE_TOLERANCE_DEG}): the base only moves with the arm folded there" if bad else None


def base_pulse_s(needed_mm: float, rate_mm_s: float, first: bool) -> float:
    """Duration of ONE bounded pulse at BASE_VX_MPS. The first pulse covers at most half the need: it measures the rate."""
    wanted = (needed_mm / 2.0 if first else needed_mm) / max(rate_mm_s, 1e-6)
    return float(min(max(wanted, BASE_PULSE_S[0]), BASE_PULSE_S[1]))


# ---------------------------------------------------------------------------------------------------------------------
# EVENTS, part 1 of 2: what the Cycle holds when NOBODY asked for events (KC_EVENTS_PATH unset). Every hook the Cycle calls
# exists and does nothing; the four hooks that wrap a callable hand back the callable itself, and an action method is
# called directly. No thread, no file, nothing imported: the legacy path stays the proven path. The object that really
# states facts (CycleEvents, below the Cycle) inherits from this one, so a hook it does not know degrades to silence in
# both modes instead of raising into a motion.
# ---------------------------------------------------------------------------------------------------------------------
def _nothing(*_args, **_kwargs):
    return None


class _NoEvents:
    enabled = False
    base_aside_mm = None  # every measured pulse's lateral_mm accumulated (CycleEvents.base_measured); None with events off, so the record states no number it did not take

    def run_action(self, action_type, method, owner, args, kwargs):
        return method(owner, *args, **kwargs)

    def reads(self, robot):
        return robot

    def base_reads(self, read_base):
        return read_base

    def polls(self, sleep):
        return sleep

    def scan_log(self, log, camera):
        return log

    def __getattr__(self, name):
        if name.startswith("_"):  # only PUBLIC hook names fall back to silence: a missing private attribute stays an error, and copy / pickle find no dunder
            raise AttributeError(name)
        return _nothing


_NO_EVENTS = _NoEvents()


def _as_action(action_type: str):
    """run_pose, grip and base_pulse are the three ACTIONS of this controller. The decorator leaves their bodies alone and
    hands the call to the events object, which calls the method exactly once and lets whatever it raises through unchanged;
    with _NoEvents that is a plain call."""
    def decorate(method):
        @functools.wraps(method)
        def acting(self, *args, **kwargs):
            return self.events.run_action(action_type, method, self, args, kwargs)
        return acting
    return decorate


# ---------------------------------------------------------------------------------------------------------------------
# MISSION MODE (the module docstring says what it changes). A Cycle built without a target, or without __init__ (the
# scripted fakes of the tests), holds target None, and every mission hook returns at once without reading or writing.
# ---------------------------------------------------------------------------------------------------------------------
def _motion_module(name: str):
    """A module of autonomy/motion, imported where it is first needed and never at module scope, so that a legacy run
    imports none of them (make_move_base puts the same directory on the path for the base primitive)."""
    directory = str(ROOT / "autonomy" / "motion")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return importlib.import_module(name)


def additive_words_apply(target) -> bool:
    """THE WORDS APPLY TO AUTO TARGETS ONLY (the module docstring): whether the additive words a run names apply to it, for
    `target` the mission target it carries (None: a run without one). They do for a run without a mission target and for a
    mission whose every target is auto; a mission with a target that declares its mode runs as the installed controller
    runs it, whatever words it names."""
    return target is None or all(declared.auto for declared in target.targets)


@contextlib.contextmanager
def _placement_policies_at_search(cycle):
    """SEARCH under the additive words (_ADDITIVE_POLICIES). With none of them, or for a mission with a target that declares
    its mode (additive_words_apply), nothing here does anything: SEARCH is the proven one, placement_plan is the proven
    function and the Cycle holds no portfolio. With multi_block the listed blocks are decided
    through a portfolio of the proven policy alone, for multi_block's binding (block_selection, THE RULE), and SEARCH from the
    scene (the word scene_search) is multi_block's (block_selection.search_from_scene: a block of several off the pad by THE
    RULE, where the proven scene search refuses): policy_search reads its scene search by its module name, so for the length
    of SEARCH ONLY that name IS multi_block's procedure, and policy_search does not change a byte. With an additive placement
    word the portfolio plans every policy the run names beside the proven one, and for the length of SEARCH ONLY the module
    name placement_plan IS that portfolio (placement_selector.Portfolio, placement_plan's own signature): SEARCH, the mission
    binding and the scene's classes read placement_plan by name through the controller view, so they all plan with it while
    no proven stage module changes a byte. Both are the proven functions again the moment SEARCH ends, however it ends, so
    the placement stage after the grasp plans with the proven placement_plan."""
    global placement_plan
    policies = placement_selector.enabled(PLACEMENT_ENVELOPE)
    if (not policies and block_selection.WORD not in PLACEMENT_ENVELOPE) or not additive_words_apply(getattr(cycle, "target", None)):
        yield
        return
    proven, scene = placement_plan, policy_search._search_from_scene
    cycle._search_portfolio = placement_selector.Portfolio(_CONTROLLER_VIEW, proven, policies)
    if policies:
        placement_plan = cycle._search_portfolio
    if block_selection.WORD in PLACEMENT_ENVELOPE:
        policy_search._search_from_scene = block_selection.search_from_scene
    try:
        yield
    finally:
        placement_plan = proven
        policy_search._search_from_scene = scene


@contextlib.contextmanager
def _advance_rule(make_rule):
    """THE PLACEMENT RULE OF ONE ADVANCE, under an additive placement word only (placement_executor.py). The frozen advance loop
    (Cycle._advance_base_held) and the capability's approach state (Cycle._approach_state) call base_advance_needed_mm BY
    NAME; for the length of this window the module name base_advance_needed_mm IS make_rule(the proven rule): the advance_
    then_reach policy's remaining need, or the proven rule held to the advance budget this run has left. No frozen byte
    changes; the rule is the proven one again the moment the window ends, however it ends. A window never opens inside
    another (a rule carries advance_rule, and the proven function none)."""
    global base_advance_needed_mm
    proven = base_advance_needed_mm
    if getattr(proven, "advance_rule", None):
        raise RuntimeError("an advance rule window never opens inside another one")
    base_advance_needed_mm = make_rule(proven)
    try:
        yield
    finally:
        base_advance_needed_mm = proven


def _mission_record() -> dict:
    """What a mission run gathers for its result file, filled in as the cycle goes. `reason_code` is the code of the raise
    site that ended the cycle (Cycle.failed, or a mission stop), `summary` is run_cycle's own summary dict."""
    return {"reason_code": None, "summary": None, "bindings": None, "selection": None, "search_observation_id": None, "placement": None,
            "exit_observation_id": None}


class Cycle:
    base_settle_s = 0.5
    base_ahead_mm = 0.0  # MEASURED displacement of the base from where this run started, along its travel axis; reported with every failure
    events = _NO_EVENTS  # a class attribute, so that a Cycle built without __init__ (the scripted fakes of the tests) has it too
    target = None  # the mission target this run carries (target_binding.MissionTarget); None is the legacy cycle, and a class attribute for the same reason
    _post_pulse_reobservation = False  # True only while measured_base_pulse re-observes the pad: the one perception a mission stop does not refuse
    # SEARCH's portfolio of placement policies (placement_selector.Portfolio), set by _placement_policies_at_search for a run that
    # names an additive word and None otherwise; kept after SEARCH for the executor guard.
    _search_portfolio = None
    perception = None  # the desktop perception client (remote_perception.RemotePerception); None: perceive runs here, as it always did
    # How many commanded motions (an executed arm plan, a gripper move, a base pulse) have ENDED in this run. A capture taken
    # at epoch N describes the scene only while the epoch is still N (target_observation_source.check_for_pulse). Counted
    # in a finally, so a motion that raised still counts; nothing reads it but the capability relay and _capture.
    motion_epoch = 0
    _capture = None  # the latest capture observe() STAMPED: its id, its monotonic stamp, its epoch, whether the base read at rest, its frame path and its stored camera pose
    # The approach state advance_base keeps while it runs (the module docstring, CAPABILITY): the observation id of the view it
    # came from, the anchor and the reach axis in PAD_FRAME, the remaining advance in mm, and the view's pad validity figures.
    # None outside advance_base: the capability's observation is meaningful only between HOLD_OBSERVE and the pulses.
    _approach = None
    _pulse_view = None  # the read-only re-observation of the last measured pulse (measured_base_pulse), the view the next approach state comes from
    runtime_live = False  # KC_RUNTIME_MODE is "live" (_run sets it): the synthetic observation is out (the module docstring)

    def __init__(self, robot, pi_ssh: str, scan_id: str, plan_only: bool, move_base=None, events=None,
                 capture_provider: str = cli.CAPTURE_PROVIDER_LEGACY, mission_port: int = 5570, target=None, perception=None):
        self.robot, self.plan_only, self.scan_id, self.move_base = robot, plan_only, scan_id, move_base
        if target is not None:
            self.target, self._mission = target, _mission_record()
        if perception is not None:
            # The client abandons an outstanding request the moment this is true: the stop flag, except while the read-only
            # re-observation after a base pulse runs (the same exemption as _perception_may_start). Read at call time.
            self.perception = perception
            perception.should_cancel = lambda: self.abort["set"] and not self._post_pulse_reobservation
        if events is not None:
            self.events = events
        self.source = self.events.reads(robot)  # where the pose and the base velocity are READ from: the robot itself unless events are on. Commands never go through it.
        self.store = ObservationStore(session_id=scan_id, **cli.store_root())
        self.session_dir = Path(self.store.session_dir)
        # WHICH PROVIDER OPENS THE CAMERA. The default is the legacy per-capture path this controller has always used,
        # so a run that names nothing behaves exactly as every recorded run did. The service is reached only when it is
        # named, and `make_camera` refuses a name it does not know rather than quietly choosing the other one.
        self.capture_provider = capture_provider
        options = {"mission_port": mission_port} if capture_provider == cli.CAPTURE_PROVIDER_SERVICE else {}
        self.camera = None if plan_only and not pi_ssh else cli.make_camera(pi_ssh, self.session_dir, capture_provider, **options)
        self.events.camera_ready(self.camera)
        self.abort = {"set": False}
        self.detector = None
        self.transitions = []

    def state(self, name: str, *, evidence=None, **fields) -> None:
        # evidence is keyword-only and is NOT one of **fields: it is taken out before the record and the line are built, so
        # both stay byte for byte what they were. It names the observations this transition rests on, for the event only.
        self.transitions.append({"state": name, "utc": cli.now_utc(), **fields})
        print(f"[STATE] {name} {json.dumps(fields, default=float)}", flush=True)
        self.events.state(name, fields, evidence)

    @staticmethod
    def _envelope_fields(**fields) -> dict:
        """State fields that exist only under an envelope word: the differential test pins the default path's transition
        record and stdout, so under the proven envelope no state gains a key. The events predicates carry the same
        numbers under every word."""
        return dict(fields) if PLACEMENT_ENVELOPE else {}

    def failed(self, code: str, message: str, *, evidence=None, **values) -> CycleFailed:
        """The CycleFailed of ONE raise site, built here so that the reason the cycle stopped is DERIVED where the decision
        was made and never read back out of the sentence. The sentence itself is unchanged: it is what the operator reads
        on stdout, and the code, the values and the evidence ride beside it. `raise self.failed(...)` is the only way this
        controller ends a cycle, and with no events configured the hook below does nothing at all."""
        self.events.cycle_failed(code, message, values, evidence)
        if self.target is not None:  # the mission result names the same code the event names
            self._mission["reason_code"] = code
        return CycleFailed(message)

    def pose(self) -> dict:
        return cli.read_pose(self.source)

    def _abort_requested(self) -> bool:
        """What the executors poll before every tick: the flag the SIGINT handler set, handed back unchanged. The first time
        it reads true, the stop is STATED from here, in normal control flow; the handler itself never emits anything."""
        requested = self.abort["set"]
        if requested:
            self.events.stop_observed(self.abort, "arm")
        return requested

    def _deps(self, holding: bool, wrist_flex_ceiling: float) -> ScanWorldDeps:
        def send(command: dict):
            sent = self.robot.send_action(cli.arm_only_command(command, wrist_flex_ceiling, keep_gripper=holding))
            self.events.command_sent()  # the FIRST command of an action is where "action started" is a fact; every later call returns at once
            return sent

        bounded = make_bounded_move_arm(self.pose, send)

        def move(target, duration_s, should_abort):
            refusal = gripper_refusal(self.pose(), holding)
            if refusal:
                raise RuntimeError(refusal)  # the executor turns this into an abort: no further motion
            commanded_s, started = duration_s * SLOW_FACTOR, time.monotonic()
            motion_ns = time.monotonic_ns()
            measured = bounded(target, commanded_s, should_abort)
            print(f"[ARM_MOVE] planner {duration_s:.2f} s x slow-factor {SLOW_FACTOR} = {commanded_s:.2f} s commanded; wall-clock {time.monotonic() - started:.2f} s", flush=True)
            self.events.timing("motion", motion_ns, "arm_segment")
            return measured

        # The wrappers below are the identity with _NoEvents. With events on they WATCH: base_reads and polls see which reads
        # one base-zero evaluation of the executor consumed (each with its stamp), scan_log learns the id of the observation a
        # capture became. None of them changes a value that passes through.
        return ScanWorldDeps(read_pose=self.pose, read_base=self.events.base_reads(lambda: cli.read_base(self.source)), move_arm=move,
                             capture=(self.camera.capture if self.camera else (lambda role: (_ for _ in ()).throw(RuntimeError("plan-only never captures")))),
                             now_utc=cli.now_utc, sleep=self.events.polls(time.sleep), should_abort=self._abort_requested,
                             log=self.events.scan_log(lambda event, **f: print(f"[{event}] {f}", flush=True), self.camera))

    @_as_action("arm_pose")
    def run_pose(self, pose, holding: bool, floor_m: float, pan_allowance: float = 0.0, label: str = "", manipulation_step: bool = True) -> dict:
        """Plan from the fresh pose, print, execute with that plan's own digest and start pose (the executor still refuses
        if the arm left the planned start), capture, perceive. Returns the measured pose, tool state and perception."""
        measured = self.pose()
        refusal = gripper_refusal(measured, holding)
        if refusal:
            raise ScanRefused(refusal)
        poses = list(pose) if isinstance(pose, (list, tuple)) else [pose]
        pose = poses[-1]
        ceiling = max([float(measured["wrist_flex"])] + [float(item.joints["wrist_flex"]) for item in poses]) + cli.WRIST_FLEX_CEILING_MARGIN_DEG
        deps = self._deps(holding, ceiling)
        common = dict(stage=1, store=self.store, calibration=CalibrationInventory.empty(), scan_id=self.scan_id, custom_poses=poses, allow_low_entry=manipulation_step,
                      min_link_height_m=floor_m, low_pan_allowance_deg=pan_allowance, manipulation_step=manipulation_step,
                      gripper_state="holding" if holding else "open", cube_held="yes" if holding else "no")
        planning_ns = time.monotonic_ns()
        plan = run_scan_world(deps, **common)
        self.events.arm_pose_planned(label or pose.pose_id, planning_ns, plan, poses, holding, floor_m, pan_allowance, self.plan_only)
        print(f"PLAN {label or pose.pose_id}: start { {k: round(v, 3) for k, v in plan.start_pose.items()} } -> target { {k: round(v, 3) for k, v in pose.joints.items()} }")
        print(f"  tool now {fmt_tool(tool_state(measured))} -> target {fmt_tool(tool_state(pose.joints))}; floor {'scan default' if floor_m is None else format(floor_m, '+.3f') + ' m'}; segments {[(s.kind, round(s.duration_s * SLOW_FACTOR, 1)) for s in plan.segments]} s commanded; token {plan.plan_digest}")
        if self.plan_only:
            print("PLAN ONLY: nothing was commanded.")
            return {"planned": True}
        before = {r["observation_id"] for r in self.store.load_all()}
        try:
            result = run_scan_world(deps, execute=True, approved_plan_digest=plan.plan_digest, approved_start_pose=plan.start_pose, **common)
        finally:
            self.motion_epoch += 1
        final = self.pose()
        self.events.action_measured(final, result.abort_reason)  # the measured state of THIS read, with the stamp of this read
        errors = {k: round(final[k] - pose.joints[k], 2) for k in SCAN_JOINTS}
        print(f"RESULT {label or pose.pose_id}: abort {result.abort_reason}; measured { {k: round(v, 3) for k, v in final.items()} }; errors {errors}; tool {fmt_tool(tool_state(final))}; base {cli.read_base(self.robot)}")
        perception = self._perceive_new(before)
        if result.abort_reason is not None:
            raise RuntimeError(f"step aborted: {result.abort_reason}")
        return {"measured": final, "tool": tool_state(final), "perception": perception}

    def _perceive_new(self, before: set) -> list:
        import cv2

        self._perception_may_start()
        first_detector, detector_ns = self.detector is None, time.monotonic_ns()
        if self.perception is not None:  # the desktop's proposals, per accepted frame: no model is made or loaded on this computer
            self.detector = self.detector or self.perception.detections
        self.detector = self.detector or (cli.make_detector() if self.target is None else self._caching_detector())
        self.events.detector_made(first_detector, detector_ns, self.detector)
        out = []
        for record in self.store.load_all():
            if record["observation_id"] in before or not record.get("raw_frame_path"):
                self.events.captured_without_frame(record, before)  # a NEW record with no frame is a failed capture: evidence of its own, and a gap
                continue
            frame_path = self.session_dir / record["raw_frame_path"]
            print(f"observation {record['observation_id']} image: {frame_path}")
            self._perception_may_start()
            perceive_ns = time.monotonic_ns()
            if self.perception is not None:
                print("\n".join(self._remote_perceive(record["observation_id"], frame_path)))
            else:
                print("\n".join(cli.perceive("wrist", cv2.imread(str(frame_path)), self.session_dir / "perception", record["observation_id"], self.detector)))
            out.append({"observation_id": record["observation_id"], "frame": str(frame_path), **json.loads((self.session_dir / "perception" / f"{record['observation_id']}.json").read_text())})
            self.events.perceived(record, out[-1], perceive_ns, frame_path, self.detector)
        self._perception_may_start()  # a result that was still being computed when the stop came is not acted on
        return out

    # -----------------------------------------------------------------------------------------------------------------
    # MISSION MODE: the stop. A perception cannot be interrupted (a frame on the robot takes up to about 74 s), but once
    # the stop flag is set none is STARTED and the result of one already running starts nothing: without this, a late
    # result would still be planned on, printed and handed to the next motion entry, which then refuses only because it
    # checks the flag itself. Legacy (no target): returns at once, the proven behaviour is untouched.
    # -----------------------------------------------------------------------------------------------------------------
    def _perception_may_start(self) -> None:
        """Raises RuntimeError (exit 4, "stopped") when this is a mission run, the stop flag is set, and this is not the
        read-only re-observation that measures where a base pulse stopped (measured_base_pulse): that one still runs, so
        that the report states where the base really is."""
        if self.target is None or self._post_pulse_reobservation or not self.abort["set"]:
            return
        self.events.stop_observed(self.abort, "perception")
        self._mission["reason_code"] = "operator_abort_perception"
        raise RuntimeError("operator abort: no perception is started once the stop is requested, and no perception result starts anything")

    def _remote_perceive(self, observation_id: str, frame_path) -> list:
        """The desktop's perception of ONE stored frame (the module docstring, DESKTOP PERCEPTION), written where perceive
        writes it. A refused answer is a CycleFailed here, before anything reads it; a stop abandons the request."""
        import remote_perception

        try:
            return self.perception.perceive(frame_path, self.session_dir / "perception", observation_id, role="wrist")
        except remote_perception.RemotePerceptionCancelled:
            self.events.stop_observed(self.abort, "perception")
            if self.target is not None:
                self._mission["reason_code"] = "operator_abort_perception"
            raise RuntimeError("operator abort: the outstanding desktop perception was abandoned, and its answer, if it comes, starts nothing")
        except remote_perception.RemotePerceptionRefused as refusal:
            raise self.failed(f"perception_remote_{refusal.reason_code}", f"the desktop perception answer for {observation_id} was refused ({refusal.reason_code}"
                              + (f", service: {refusal.service_reason}" if refusal.service_reason else "") + "); nothing is decided on it and the arm holds",
                              observation_id=observation_id, service_reason=refusal.service_reason)

    def _caching_detector(self):
        """The detector perceive is handed in mission mode: the same detector, wrapped so that the cube proposals of every
        frame are kept (cube_candidates.CubeDetectionCache forwards every call unchanged and returns the detector's own
        answer), so that every verified cube of a frame can be listed without running the model again."""
        import cube_candidates

        return cube_candidates.CubeDetectionCache(cli.make_detector())

    def observe(self, holding: bool) -> dict:
        """Capture + perception without any command: a zero-length plan is not needed, the camera and the store are used directly."""
        from world_observation import BASE_VELOCITY_KEYS, build_observation, new_observation_id
        from frame_illumination import illumination_diagnostics

        self._perception_may_start()  # a capture is the start of a perception
        before = {r["observation_id"] for r in self.store.load_all()}
        joints_before = self.pose()
        capture_ns, epoch = time.monotonic_ns(), self.motion_epoch  # BEFORE the capture is asked for: the conservative age of what it returns
        captured = self.camera.capture("wrist")
        joints_after, base = self.pose(), cli.read_base(self.robot)
        timestamp = cli.now_utc()
        observation_id = new_observation_id(timestamp, "wrist", 1)
        observation = build_observation(observation_id=observation_id, timestamp_utc=timestamp, camera_role="wrist", identity_fingerprint=captured.record.identity_fingerprint,
                                        image_orientation="direct-v4l-unrotated", frame_shape=captured.record.frame_shape,
                                        raw_frame_path=self.store.frame_relative_path(observation_id) if captured.frame is not None else None,
                                        measured_joints_deg=joints_before, measured_joints_post_capture_deg=joints_after, base_velocity={k: float(base[k]) for k in BASE_VELOCITY_KEYS},
                                        calibration=CalibrationInventory.empty(), scan=None, gripper_state="holding" if holding else "open", cube_held="yes" if holding else "no",
                                        illumination=illumination_diagnostics(captured.frame), lifecycle=captured.record.to_dict(), atlas_annotation=None)
        self.store.save(observation, captured.frame)
        # stationary: the base read AT REST at this capture (base_rest.reads_at_rest: no wheel above one velocity count; the
        # stored record's base_velocity, read right after the frame). Formerly exact zero on all three axes, which one count of
        # dither on a held wheel failed (operator ruling 2026-09-27). The capability's observation (target_observation_source.
        # resolve) is refused without it.
        self._capture = {"observation_id": observation_id, "capture_monotonic_ns": capture_ns, "motion_epoch": epoch,
                         "stationary": reads_at_rest({k: float(base[k]) for k in BASE_VELOCITY_KEYS}), "raw_frame_path": observation.get("raw_frame_path"),
                         "camera_pose": dict(observation.get("camera_pose") or {})}
        self.events.capture_timing(self.camera, observation_id)
        return {"measured": joints_after, "tool": tool_state(joints_after), "perception": self._perceive_new(before)}

    @_as_action("gripper")
    def grip(self, target: float, label: str) -> dict:
        """The gripper ALONE to a proven value: arm joints held at their measured values, base exactly zero, the proven
        0.5 s move (x slow factor) and 0.75 s hold; base-zero confirmed before and after; then a wrist observation."""
        if target not in (GRIPPER_LATCH, GRIPPER_OPEN):
            raise ScanRefused("the gripper is only ever commanded to the two physically proven values")
        measured = self.pose()
        digest = hashlib.sha256(json.dumps([label, target, [[k, round(float(measured[k]), 1)] for k in SCAN_JOINTS]]).encode()).hexdigest()[:16]
        print(f"PLAN grip:{label}: gripper {measured['gripper']:.2f} -> {target}; arm held at { {k: round(measured[k], 3) for k in SCAN_JOINTS} }; {GRIPPER_MOVE_S * SLOW_FACTOR:.1f} s move + {GRIPPER_HOLD_S} s hold; token {digest}")
        self.events.gripper_planned(label, target, measured, digest, self.plan_only)
        if self.plan_only:
            print("PLAN ONLY: nothing was commanded.")
            return {"planned": True}
        if not _base_is_zero(self._deps(False, 1e9)):
            raise ScanRefused("base velocity is not confirmed zero; the gripper is not commanded")
        current = {f"arm_{k}.pos": float(v) for k, v in measured.items()}
        goal = {**current, "arm_gripper.pos": float(target)}
        ceiling = float(measured["wrist_flex"]) + 0.2

        def send_tick(waypoint):
            started = time.perf_counter()
            self.robot.send_action(cli.arm_only_command(manipulation_action_to_robot_command(freeze_wrist_roll(waypoint, float(measured["wrist_roll"]))), ceiling, keep_gripper=True))
            self.events.command_sent()  # inside the tick's own time budget: the sleep below is what is left of the period
            time.sleep(max(1.0 / SCAN_CONTROL_FPS - (time.perf_counter() - started), 0.0))

        motion_ns = time.monotonic_ns()
        try:
            status = run_canonical_handoff(current, goal, send_tick, self._abort_requested, n_steps=max(1, round(GRIPPER_MOVE_S * SLOW_FACTOR * SCAN_CONTROL_FPS)))
        finally:
            self.motion_epoch += 1
        if status == REFUSED_DELTA_TOO_LARGE:
            raise ScanRefused("gripper per-tick step too large; nothing was sent")
        if status == ABORTED and self.target is not None:  # mission mode: a stopped gripper move is not followed by the hold, a read to judge it, or an observation
            self._mission["reason_code"] = "operator_abort_gripper"
            raise RuntimeError("operator abort during the gripper move: no further waypoint was sent, and nothing is observed or judged after it")
        self.events.timing("motion", motion_ns, "gripper_move")
        time.sleep(GRIPPER_HOLD_S)
        after = self.pose()
        self.events.action_measured(after, None, gripper_read=True)  # this read's gripper value is what GRASP_CLOSE judges
        moved = {k: round(after[k] - measured[k], 2) for k in SCAN_JOINTS}
        print(f"RESULT grip:{label}: measured gripper {after['gripper']:.3f} (commanded {target}); arm joints moved {moved}; base zero after: {_base_is_zero(self._deps(False, 1e9))}")
        holding = target == GRIPPER_LATCH
        return {"gripper": float(after["gripper"]), **self.observe(holding=holding and closed_on_something(float(after["gripper"])))}


    # -----------------------------------------------------------------------------------------------------------------
    # The base. It is never commanded through the arm path (arm_only_command refuses any non-zero base velocity); it moves
    # only here, in bounded pulses of the proven gateway primitive, with the arm folded at the low scan pose and HELD, and
    # every pulse is followed by stop, base-zero confirmation and a fresh pad observation that MEASURES what it did.
    # -----------------------------------------------------------------------------------------------------------------
    def base_is_zero(self) -> bool:
        return _base_is_zero(self._deps(False, 1e9))

    @staticmethod
    def _pad_frame(result: dict):
        records = result.get("perception") or []
        template = (records[-1].get("pad_by_template") or {}) if records else {}
        return template if template.get("pad_frame_valid") and template.get("homography_image_to_pad") and template.get("homography_pad_to_image") else None

    @staticmethod
    def _approach_state(view, anchor, axis) -> dict:
        """The approach state the capability's observation is resolved from (target_observation_source.resolve): the id
        of the view's record, the anchor and the reach axis in PAD_FRAME, the remaining advance (base_advance_needed_mm,
        the executor's own quantity; None when no bounded advance covers it) and the view's pad validity figures
        (pad_template_localizer: pad_frame_valid, landmark_inliers, reprojection_rms_px). Read, never measured here."""
        records = (view or {}).get("perception") or []
        record = records[-1] if records else {}
        pad = record.get("pad_by_template") or {}
        return {"observation_id": record.get("observation_id"), "anchor_pad_m": [float(anchor[0]), float(anchor[1])], "axis_pad": [float(axis[0]), float(axis[1])],
                "need_mm": base_advance_needed_mm(anchor, axis), "pad_frame_valid": bool(pad.get("pad_frame_valid")),
                "landmark_inliers": pad.get("landmark_inliers"), "reprojection_rms_px": pad.get("reprojection_rms_px")}

    @_as_action("base_pulse")
    def base_pulse(self, vx_mps: float, duration_s: float, holding: bool) -> None:
        """ONE bounded base translation. Refused unless the arm is folded at the low scan pose, the gripper measures its
        declared phase and the base is confirmed stationary. Afterwards the primitive's own stop confirmation AND this
        executor's base-zero reads must both hold, and the arm joints must not have moved: otherwise the cycle fails HOLDING."""
        if self.move_base is None:
            raise self.failed("base_primitive_not_wired", "no base primitive is wired into this run; the arm holds")
        if self.runtime_live and self.target is None and vx_mps > 0:  # the module docstring, THE SYNTHETIC OBSERVATION IS OUT OF A LIVE RUN
            raise self.failed(LIVE_FORWARD_EXCLUDED, "a forward base pulse of a LIVE run without a mission target would be the in-process "
                              "primitive fed the synthetic observation, which is for rehearsal only; nothing was commanded, the arm holds")
        if abs(vx_mps) != BASE_VX_MPS or not BASE_PULSE_S[0] <= duration_s <= BASE_PULSE_S[1]:
            raise self.failed("base_pulse_outside_its_bounds", f"a base pulse runs at exactly {BASE_VX_MPS} m/s for {BASE_PULSE_S[0]}..{BASE_PULSE_S[1]} s, got {vx_mps} m/s for {duration_s} s",
                              vx_mps=vx_mps, duration_s=duration_s, base_vx_mps=BASE_VX_MPS, pulse_min_s=BASE_PULSE_S[0], pulse_max_s=BASE_PULSE_S[1])
        if self.abort["set"]:
            self.events.stop_observed(self.abort, "base")
            raise self.failed("operator_abort_before_base_pulse", "operator abort: no further base pulse is commanded", base_ahead_mm=self.base_ahead_mm)
        before = self.pose()
        # The two refusals are computed exactly as they were (the posture one is not reached, and HOME_FILE is not read,
        # when the gripper already refuses): the only thing added is WHICH of the two the sentence came from.
        code, refusal, home = "base_pulse_refused_gripper", gripper_refusal(before, holding), None
        if not refusal:
            home = json.loads(HOME_FILE.read_text())["measured_joints_deg"]
            code, refusal = "base_pulse_refused_posture", base_posture_refusal(before, home)
        if refusal:
            self.events.base_pulse_refusal(code, before, holding, home)
            raise self.failed(code, f"BASE pulse refused: {refusal}", holding=holding)
        if not self.base_is_zero():
            raise self.failed("base_not_stationary_before", "BASE pulse refused: the base is not confirmed stationary before it")
        print(f"PLAN base pulse: vx {vx_mps:+.3f} m/s for {duration_s:.2f} s through the gateway; arm HELD at { {k: round(before[k], 3) for k in SCAN_JOINTS} }", flush=True)
        self.events.base_pulse_planned(vx_mps, duration_s, before, holding, self.move_base)
        try:
            stopped = bool(self.move_base(vx_mps, duration_s))
        finally:
            self.motion_epoch += 1
        self.events.base_stop_confirmed(stopped, self.move_base)  # the primitive's own stop confirmation, with the reads it took when they are stamped
        time.sleep(self.base_settle_s)  # the second client of the primitive has just left: let this client's observation stream settle
        zero, after = self.base_is_zero(), self.pose()
        self.events.action_measured(after, None)
        drift = {k: round(after[k] - before[k], 2) for k in SCAN_JOINTS}
        print(f"RESULT base pulse: stop confirmed by the primitive {stopped}; base zero by this executor {zero}; arm joints moved {drift}", flush=True)
        relay_refusal = self._capability_relay_refusal(vx_mps)  # None in the legacy cycle and for every pulse the relay did not run
        if relay_refusal is not None and zero and max(abs(v) for v in drift.values()) <= BASE_MAX_ARM_DRIFT_DEG:
            # This executor's own base-zero reads and the arm drift were taken first and both hold; only then is the relay's
            # refusal the reason (a relay that refused before any motion confirmed no stop, and the base did not move). When
            # either does NOT hold, the two refusals below state that measured fact instead, and the cycle ends there as well.
            if relay_refusal == RELAY_STOPPED_BEFORE_MOTION:  # the relay saw this run's stop before it commanded anything: the stop, as the check above states it
                self.events.stop_observed(self.abort, "base")
                raise self.failed("operator_abort_before_capability_pulse", "operator abort: the capability relay saw the stop before it commanded anything; no base pulse was commanded",
                                  base_ahead_mm=self.base_ahead_mm)
            detail = self._capability_relay_detail(relay_refusal)  # the relay's own detail of that refusal (the source's reasons, say), when it has one
            raise self.failed("base_capability_output_refused", f"BASE pulse: the capability relay refused ({relay_refusal}{': ' + detail if detail else ''}); the base reads zero and the arm held; "
                              "nothing further is commanded", capability_refusal=relay_refusal, capability_refusal_detail=detail, stop_confirmed_by_primitive=stopped,
                              base_zero_by_this_executor=zero, holding=holding)
        if not (stopped and zero):
            raise self.failed("base_not_confirmed_after", "BASE not confirmed stationary after the pulse; nothing further is commanded",
                              stop_confirmed_by_primitive=stopped, base_zero_by_this_executor=zero)
        if max(abs(v) for v in drift.values()) > BASE_MAX_ARM_DRIFT_DEG:
            self.events.predicate("arm_held_during_base_translation", False, measured=max(abs(v) for v in drift.values()), threshold=BASE_MAX_ARM_DRIFT_DEG, unit="deg",
                                  scalars=("arm_joint_drift_deg",), empirical=("BASE_MAX_ARM_DRIFT_DEG",), stamp_of=after)
            raise self.failed("base_arm_drifted", f"the arm moved during the base translation ({drift}); the pad frames before and after do not measure the base",
                              arm_drift_deg=max(abs(v) for v in drift.values()), arm_drift_tolerance_deg=BASE_MAX_ARM_DRIFT_DEG)

    def _capability_relay_refusal(self, vx_mps: float):
        """MISSION MODE, a target that requires the capability, a FORWARD pulse: why the relay refused it (its own
        last_refusal), or capability_changed_between_pulses when this pulse's snapshot names another generation or pid than
        the first pulse of this run did (each pulse binds its answers to its own snapshot; one run carries one capability).
        None otherwise, and always None in the legacy cycle."""
        if self.target is None or not self.target.carries_capability or not vx_mps > 0:
            return None
        refusal = getattr(self.move_base, "last_refusal", None)
        if refusal:
            return str(refusal)
        pulses = [pulse for pulse in (getattr(getattr(self.move_base, "capability", None), "pulses", None) or []) if isinstance(pulse, dict) and pulse.get("slot_generation") is not None]
        if pulses and (pulses[-1].get("slot_generation"), pulses[-1].get("producer_pid")) != (pulses[0].get("slot_generation"), pulses[0].get("producer_pid")):
            return "capability_changed_between_pulses"
        return None

    def _capability_relay_detail(self, relay_refusal: str):
        """The relay's own detail of the refusal it just made (capability_pulse's last_evidence refusal_detail: for
        target_observation_unavailable, the source's reason codes), or None when the relay has none or it is about another
        refusal. Codes and class names only, never a value."""
        evidence = getattr(getattr(self.move_base, "capability", None), "last_evidence", None)
        if not isinstance(evidence, dict) or evidence.get("refusal") != relay_refusal:
            return None
        detail = evidence.get("refusal_detail")
        return str(detail) if detail else None

    def _reobserve_after_pulse(self, holding: bool) -> dict:
        """The read-only re-observation that measures where a base pulse stopped. It runs even after a stop was requested
        (mission mode refuses every OTHER perception then): the report must state where the base really is."""
        self._post_pulse_reobservation = True
        try:
            return self.observe(holding)
        finally:
            self._post_pulse_reobservation = False

    def measured_base_pulse(self, vx_mps: float, duration_s: float, holding: bool, reference_pad_m, direction, frame_before: dict) -> tuple:
        """Pulse, stop, re-observe the pad, MEASURE. Returns (displacement, the new pad frame)."""
        measurement_ns = time.monotonic_ns()
        self.base_pulse(vx_mps, duration_s, holding)
        self._pulse_view = self._reobserve_after_pulse(holding)  # kept: advance_base takes its next approach state from this view
        frame_after = self._pad_frame(self._pulse_view)
        self.events.timing("base_measurement", measurement_ns, "base_pulse_and_reobserve")
        if frame_after is None:
            raise self.failed("base_pulse_no_pad_frame_after", "PAD_FRAME is not valid after the base pulse: the displacement is unknown; nothing further is commanded",
                              vx_mps=vx_mps, duration_s=duration_s, base_ahead_mm=self.base_ahead_mm)
        moved = base_displacement(reference_pad_m, direction, frame_before["homography_pad_to_image"], frame_after["homography_image_to_pad"])
        self.state("BASE_MEASURED", vx_mps=vx_mps, duration_s=round(duration_s, 3), advance_mm=round(moved["advance_mm"], 1), lateral_mm=round(moved["lateral_mm"], 1), distortion_mm=round(moved["distortion_mm"], 2),
                   rate_mm_s=round(abs(moved["advance_mm"]) / duration_s, 1), evidence=(frame_before, frame_after))
        signed = moved["advance_mm"] * math.copysign(1.0, vx_mps)
        if (moved["distortion_mm"] > BASE_MAX_DISTORTION_MM or abs(moved["lateral_mm"]) > BASE_MAX_LATERAL_MM + BASE_MAX_AXIS_SLOPE * abs(moved["advance_mm"]) or signed < -5.0
                or signed > BASE_RATE_BOUNDS_MM_S[1] * duration_s + BASE_PLAUSIBLE_SLACK_MM):
            self.events.base_measured(False, moved, frame_before, frame_after, holding)
            raise self.failed("base_measurement_implausible", f"the two pad frames do not measure a plausible bounded translation along the expected axis for a {duration_s:.2f} s pulse: {moved}; nothing further is commanded",
                              evidence=(frame_before, frame_after), advance_mm=moved["advance_mm"], lateral_mm=moved["lateral_mm"], distortion_mm=moved["distortion_mm"], duration_s=duration_s,
                              max_distortion_mm=BASE_MAX_DISTORTION_MM, max_lateral_mm=BASE_MAX_LATERAL_MM, max_rate_mm_s=BASE_RATE_BOUNDS_MM_S[1], plausible_slack_mm=BASE_PLAUSIBLE_SLACK_MM)
        self.events.base_measured(True, moved, frame_before, frame_after, holding)
        self.base_ahead_mm += moved["advance_mm"]
        if self.abort["set"]:  # checked AFTER the read-only re-observation, so that the report states where the base really is
            self.events.stop_observed(self.abort, "base_after_pulse")
            raise self.failed("operator_abort_base_path", "operator abort during the base pulse: the base is confirmed stationary and its displacement is measured; nothing further is commanded",
                              evidence=(frame_before, frame_after), advance_mm=moved["advance_mm"], base_ahead_mm=self.base_ahead_mm)
        return moved, frame_after

    def advance_base(self, anchor, direction) -> tuple:
        """HOLD_OBSERVE (the low scan pose with the cube held), then bounded forward pulses until the anchor itself lies the
        required margin plus the buffer inside the pad. Returns (anchor, total advance, last measured rate).

        THE APPROACH STATE (_approach, the module docstring's CAPABILITY): kept from the view the loop pulses on, before
        the first pulse and again after every measured one, and None once this returns or raises. For a run that carries
        the capability the view before the first pulse is a STAMPED observe() taken right after the HOLD_OBSERVE rise:
        run_pose never stamps _capture, so without it the latest stamped capture would be the grasp's, whose motion epoch
        the lifts and the rise have left behind, and the relay would refuse every pulse (observation_epoch_changed). The
        legacy cycle and a mission run without the capability take no extra capture and are what they were."""
        try:
            return self._advance_base_held(anchor, direction)
        finally:
            self._approach = None

    def _advance_base_held(self, anchor, direction) -> tuple:
        home = json.loads(HOME_FILE.read_text())["measured_joints_deg"]
        view = self.run_pose(cli.pregrasp_return_poses(home)[0], True, None, label="HOLD_OBSERVE rise to the low scan pose (cube held)")
        frame = self._pad_frame(view)
        self.state("HOLD_OBSERVE", pad_frame_valid=frame is not None, anchor_pad_m=[round(v, 4) for v in anchor], anchor_margin_mm=round(placed_margin_mm(anchor), 1), evidence=view)
        self.events.predicate("pad_frame_valid_while_holding", frame is not None, frames=view)
        if frame is None:
            raise self.failed("hold_observe_no_pad_frame", "HOLD_OBSERVE: PAD_FRAME is not valid from the low scan pose with the cube held; the base never moves blind; the cube stays held",
                              evidence=view, anchor_margin_mm=placed_margin_mm(anchor))
        total, rate, axis = 0.0, BASE_RATE_PRIOR_MM_S, direction  # the base axis is the camera axis only until a pulse has MEASURED it
        if self.target is not None and self.target.carries_capability:
            # The approach view the capability is fed: a stamped capture from the stationary base (the docstring). Its pad
            # frame is the loop's, so the observation the relay publishes and the frame the displacement is measured
            # against are one and the same view.
            view = self.observe(True)
            frame = self._pad_frame(view)
            if frame is None:
                raise self.failed("approach_view_no_pad_frame", "APPROACH VIEW: PAD_FRAME is not valid in the stamped capture the capability is fed, taken from the low scan pose with the cube held; "
                                  "the base never moves blind; the cube stays held", evidence=view, anchor_margin_mm=placed_margin_mm(anchor))
        self._approach = self._approach_state(view, anchor, axis)
        for pulse in range(BASE_MAX_PULSES):
            need = base_advance_needed_mm(anchor, axis)
            if need is not None and need <= BASE_ADVANCE_TOLERANCE_MM:
                break
            if need is None or total + need > BASE_MAX_TOTAL_ADVANCE_MM:
                raise self.failed("base_advance_exceeds_bound", f"the placement needs more than the bounded total base advance ({BASE_MAX_TOTAL_ADVANCE_MM:.0f} mm; {total:.0f} mm measured so far); the cube stays held",
                                  needed_mm=need, advanced_so_far_mm=total, bounded_total_advance_mm=BASE_MAX_TOTAL_ADVANCE_MM, pulse=pulse)
            duration = base_pulse_s(need, rate, first=pulse == 0)
            self.state("BASE_ADVANCE", pulse=pulse, needed_mm=round(need, 1), duration_s=round(duration, 3), assumed_rate_mm_s=round(rate, 1), axis_pad=[round(v, 3) for v in axis])
            moved, frame = self.measured_base_pulse(BASE_VX_MPS, duration, True, anchor, axis, frame)
            step = float(np.hypot(*moved["shift_pad_mm"]))
            anchor, total = moved["anchor_pad_m"], total + step
            if step > 10.0:
                axis = (moved["shift_pad_mm"][0] / step, moved["shift_pad_mm"][1] / step)
            rate = min(max(step / duration, BASE_RATE_BOUNDS_MM_S[0]), BASE_RATE_BOUNDS_MM_S[1])
            self._approach = self._approach_state(self._pulse_view, anchor, axis)  # the re-observation's view, the moved anchor, the measured axis
            if total > BASE_MAX_TOTAL_ADVANCE_MM:
                raise self.failed("base_advanced_past_bound", f"the base has advanced {total:.0f} mm, more than the bounded total ({BASE_MAX_TOTAL_ADVANCE_MM:.0f} mm); nothing further is commanded, the cube stays held",
                                  advanced_mm=total, bounded_total_advance_mm=BASE_MAX_TOTAL_ADVANCE_MM, pulse=pulse)
        return anchor, total, rate, axis

    def return_base(self, advanced_mm: float, reference_pad_m, direction, rate: float) -> tuple:
        """After the release, gripper open, arm settled at the low scan pose: bounded pulses back to where the cycle started,
        each one measured against the pad. Returns the remaining displacement (+ = still ahead of the start)."""
        frame = self._pad_frame(self.observe(False))
        if frame is None:
            raise self.failed("base_return_no_pad_frame", f"BASE_RETURN: PAD_FRAME is not valid; the base stays {advanced_mm:.0f} mm ahead of its start", advanced_mm=advanced_mm)
        remaining, sideways = advanced_mm, 0.0
        for pulse in range(BASE_MAX_PULSES):
            if abs(remaining) <= BASE_RETURN_TOLERANCE_MM:
                break
            duration = base_pulse_s(abs(remaining) - 3.0, rate, first=False)
            self.state("BASE_RETURN", pulse=pulse, remaining_mm=round(remaining, 1), duration_s=round(duration, 3))
            moved, frame = self.measured_base_pulse(-BASE_VX_MPS if remaining > 0 else BASE_VX_MPS, duration, False, reference_pad_m, direction, frame)
            reference_pad_m, remaining, sideways = moved["anchor_pad_m"], remaining + moved["advance_mm"], sideways + moved["lateral_mm"]
            rate = min(max(abs(moved["advance_mm"]) / duration, BASE_RATE_BOUNDS_MM_S[0]), BASE_RATE_BOUNDS_MM_S[1])
        return remaining, sideways

    def base_probe(self, signed_duration_s: float, holding: bool) -> None:
        """Supervised single action: observe the pad, ONE pulse (sign = direction), re-observe, report the measured displacement."""
        frame = self._pad_frame(self.observe(holding))
        if frame is None:
            raise self.failed("base_probe_no_pad_frame", "base probe: PAD_FRAME is not valid from here; the base never moves blind")
        reference = [float(v) for v in _through(frame["homography_image_to_pad"], LOW_POSE_REF_CONTACT_PX)]
        direction = reach_direction_pad(frame["homography_image_to_pad"], LOW_POSE_REF_CONTACT_PX)
        print(f"base probe: reference point PAD {[round(v, 4) for v in reference]}, reach direction in PAD_FRAME {[round(v, 3) for v in direction]}")
        if self.plan_only:
            print("PLAN ONLY: nothing was commanded.")
            return
        self.measured_base_pulse(math.copysign(BASE_VX_MPS, signed_duration_s), abs(signed_duration_s), holding, reference, direction, frame)

    # -----------------------------------------------------------------------------------------------------------------
    # The full cycle. Every transition is printed as [STATE]; a state advances only on its physical predicate.
    # -----------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _cube(result: dict):
        records = result.get("perception") or []
        return records[-1] if records and records[-1]["cube"]["detected"] and records[-1]["cube"].get("bbox_xyxy") else None

    def _sweep_sector_outcome(self, view: dict):
        """One sweep sector's outcome: policy_search.sweep_sector_outcome."""
        return policy_search.sweep_sector_outcome(self, _CONTROLLER_VIEW, view)

    def _search_sweep(self, search: dict, measured: dict):
        """SEARCH SWEEP (the module docstring): policy_search.search_sweep."""
        return policy_search.search_sweep(self, _CONTROLLER_VIEW, search, measured)

    @staticmethod
    def _why_no_cube(result: dict) -> str:
        """WHY the recognizer reported no cube, IN ITS OWN WORDS, or a statement that it did not say.

        The recognizer already writes one note per proposal it evaluated: whether the proposal verified, and when it
        did not, every limit it failed. Run console-20260922T194137Z ended `align_cube_not_detected` while that list
        said the cube was proposed at 0.664 and rejected on box size alone, and the operator was instead shown a
        guess at two causes that the same record refutes. Nothing here interprets the notes: they are the recognizer's
        sentences, passed through.
        """
        records = result.get("perception") or []
        if not records:
            return "no perception record was produced for this view"
        notes = [str(note) for note in (records[-1].get("cube") or {}).get("notes") or []]
        return "; ".join(notes) if notes else "the recognizer recorded no reason"

    def _frame(self, result: dict):
        import cv2

        return cv2.imread(result["perception"][-1]["frame"])

    @staticmethod
    def _home_pan_deg() -> float:
        """The pan of the saved low scan pose: the pan EXIT and the HOLD_OBSERVE rise plan their one pose at."""
        return float(json.loads(HOME_FILE.read_text())["measured_joints_deg"]["shoulder_pan"])

    offset_realized = None  # (delta commanded, delta measured, chord) of the last RELEASE_OFFSET_MEASURED, for the tests

    def _release_offset_plan(self, anchor, direction, grasp_pan: float, frame_pan: float, grasp_out: float, fk_travel: float, seen):
        """RELEASE_OFFSET (T6 step 3a, under the word side_offset only): policy_placement.plan_release_offset."""
        return policy_placement.plan_release_offset(self, _CONTROLLER_VIEW, anchor, direction, grasp_pan, frame_pan, grasp_out, fk_travel, seen)

    def _release_offset_realized(self, plan: dict, over: dict, anchor, direction, grasp_pan: float, frame_pan: float) -> tuple:
        """RELEASE_OFFSET_MEASURED (T6 step 3a): policy_placement.release_offset_realized."""
        return policy_placement.release_offset_realized(self, _CONTROLLER_VIEW, plan, over, anchor, direction, grasp_pan, frame_pan)

    def _refuse_pregrasp_inside_grasp_radius(self, coarse: dict, seen) -> None:
        """THE NEAR BLOCK, the pickup band: policy_align.refuse_pickup_band."""
        policy_align.refuse_pickup_band(self, _CONTROLLER_VIEW, coarse, seen)

    def _refuse_pregrasp_beyond_pan_bounds(self, coarse_pan_deg: float, pan_now_deg: float, seen) -> None:
        """RETURN BOUND at SEARCH: policy_search.refuse_pregrasp_beyond_pan_bounds."""
        policy_search.refuse_pregrasp_beyond_pan_bounds(self, _CONTROLLER_VIEW, coarse_pan_deg, pan_now_deg, seen)

    def _refuse_trim_beyond_return_bound(self, view: dict, trim_deg: float) -> None:
        """RETURN BOUND through ALIGN: policy_align.refuse_trim_beyond_return_bound."""
        policy_align.refuse_trim_beyond_return_bound(self, _CONTROLLER_VIEW, view, trim_deg)

    def return_to_low_pose(self) -> dict:
        """EXIT to the saved low scan pose, then SETTLE: re-command the pose the arm actually reached, so every goal equals
        its measured position. Without it the elbow stays about 2.5 deg from its goal and the verify-only host refuses its
        next start (seen 2026-09-20)."""
        home = json.loads(HOME_FILE.read_text())["measured_joints_deg"]
        final = self.run_pose(cli.pregrasp_return_poses(home)[0], False, None, label="EXIT to the low scan pose")
        if not self.plan_only:
            reached = {k: round(float(final["measured"][k]), 3) for k in SCAN_JOINTS}
            self.run_pose(cli._pose("settle-low", "FAR", reached), False, None, label="SETTLE goals on the measured pose")
        return final

    def _carry_arm_only(self, carry: dict, grasp_out: float, travel_mm: float) -> dict:
        """CARRY arm-only: policy_placement.carry_arm_only."""
        return policy_placement.carry_arm_only(self, _CONTROLLER_VIEW, carry, grasp_out, travel_mm)

    # -----------------------------------------------------------------------------------------------------------------
    # MISSION MODE: which cube is this target's. Every decision is target_binding's, taken on what THIS view measured with
    # this module's own proven functions (placed_margin_mm; placement_plan from the SEARCH inputs, exactly as the SEARCH
    # feasibility check calls it). The cubes are cube_candidates' list, read beside the frozen record, never instead of it.
    # Nothing here commands anything; every refusal is a failed() before the motion it would have allowed.
    # -----------------------------------------------------------------------------------------------------------------
    def _mission_rules(self):
        """The predicates' distances: the proven PLACED margin, and the claim and match radii the mission target file
        carries. mission_refusal refused a file without radii before the robot existed, so this never invents one."""
        return self.target.rules(PLACED_MIN_EDGE_MARGIN_MM)

    def _mission_candidates(self, record: dict) -> tuple:
        """(every verified cube of this record's frame, None, None), or ([], a reason code, the enumeration's own refusal
        code, the class of the error the listing raised, or None). A list that refused, that raised (its sidecar could not
        be written, say), or that stopped with proposals left (complete false), is never used: that a cube is the ONLY
        candidate near its binding, or the block on the pad nearest this run's release, is claimed on a list that ran out
        of cubes or not at all. Listing is CPU work on a frame already perceived; a stop refuses it like a perception."""
        self._perception_may_start()
        import cv2
        import cube_candidates

        image = cv2.imread(str(record["frame"])) if record.get("frame") and record.get("observation_id") else None
        if image is None:
            return [], "mission_candidates_frame_unreadable", None
        passes = cube_candidates.MAX_PASSES
        while True:
            try:
                listing = cube_candidates.enumerate_observation(image, self.detector, self.session_dir / "perception", record["observation_id"], max_passes=passes)
            except Exception as error:  # noqa: BLE001 -- e.g. the sidecar cannot be written. At the EXIT view a traceback here would skip the base return
                return [], "mission_candidates_failed", type(error).__name__
            self._perception_may_start()
            if listing.get("refused"):
                return [], "mission_candidates_refused", str(listing["refused"])
            if listing.get("complete"):
                return list(listing.get("cubes") or []), None, None
            # THE LISTING BUDGET (target_binding.LISTING_ON_PAD_EXEMPT_PASSES): stopped at the pass limit, list again with one
            # more pass for every block it listed on the pad, never past the exemption; anything else stays incomplete
            wanted = cube_candidates.MAX_PASSES + min(self._listed_on_pad(listing), target_binding.LISTING_ON_PAD_EXEMPT_PASSES)
            if len(listing.get("passes") or ()) != passes or wanted <= passes:
                return [], "mission_candidates_incomplete", None
            passes = wanted

    @staticmethod
    def _listed_on_pad(listing: dict) -> int:
        """How many of a listing's blocks lie on the pad by the proven PLACED margin; a reading that is not a position is not."""
        count = 0
        for entry in listing.get("cubes") or ():
            centre = (entry.get("cube_by_template") or {}).get("cube_center_pad_m") if isinstance(entry, dict) else None
            try:
                count += bool(centre) and placed_margin_mm(centre) >= PLACED_MIN_EDGE_MARGIN_MM
            except (TypeError, ValueError, IndexError):
                continue
        return count

    @staticmethod
    def _mission_seen_cube(entry: dict, frame, pan_deg):
        """One listed cube as target_binding reads it. At SEARCH, `frame` (that view's own pad homography) and the measured
        pan are given and the mode is placement_plan's for THIS cube: its own contact pixel, reach direction and coarse grasp
        radius. Elsewhere both are None and so is the mode. TypeError / ValueError for a reading that is not a position."""
        centre = (entry.get("cube_by_template") or {}).get("cube_center_pad_m")
        if not centre:
            return target_binding.SeenCube(None)
        mode, contact = None, (entry.get("cube") or {}).get("contact_px")
        if frame is not None and contact:
            coarse = coarse_pregrasp_from_low_pose(contact, pan_deg)
            direction = reach_direction_pad(frame["homography_image_to_pad"], contact)
            try:
                mode = placement_plan(centre, direction, max(coarse["cube_out_mm"], 0.0), PLACE_SIDE_DRIFT_ALLOWANCE_MM)["mode"]
            except CycleFailed:
                # no bounded placement: nobody's candidate for a declared mode, and an auto target's refusal names the limit
                return target_binding.SeenCube(tuple(centre), placed_margin_mm(centre), None, placement_envelope.explain_refusal(
                    stage_policy.ControllerView(globals()), centre, direction, max(coarse["cube_out_mm"], 0.0), PLACE_SIDE_DRIFT_ALLOWANCE_MM))
        return target_binding.SeenCube(tuple(centre), placed_margin_mm(centre), mode)

    @staticmethod
    def _mission_seen_decision(entry: dict, frame, pan_deg, portfolio) -> tuple:
        """_mission_seen_cube under the additive words: (the SeenCube, the SEARCH decision of placement_selector or None). The
        mode is the chosen plan's among every policy the run names (placement_selector, THE CHOICE), from the same inputs
        _mission_seen_cube hands placement_plan; a block no policy places carries the refusal's values as its explanation (the
        best near-miss, its deficit and its checked hint). Without a SEARCH frame or a contact pixel there is no decision and no
        mode, as there. TypeError / ValueError for a reading that is not a position."""
        centre = (entry.get("cube_by_template") or {}).get("cube_center_pad_m")
        if not centre:
            return target_binding.SeenCube(None), None
        contact = (entry.get("cube") or {}).get("contact_px")
        if frame is None or not contact:
            return target_binding.SeenCube(tuple(centre), placed_margin_mm(centre), None), None
        coarse = coarse_pregrasp_from_low_pose(contact, pan_deg)
        direction = reach_direction_pad(frame["homography_image_to_pad"], contact)
        decision = portfolio.decide(centre, direction, max(coarse["cube_out_mm"], 0.0), PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        if decision.selected is None:
            return target_binding.SeenCube(tuple(centre), placed_margin_mm(centre), None, decision.explanation), decision
        return target_binding.SeenCube(tuple(centre), placed_margin_mm(centre), decision.selected.mode), decision

    def _mission_search(self, search: dict, measured: dict):
        """SEARCH for a mission target. Returns the record the rest of the cycle carries: the frozen record itself when the
        selected cube is the one it names (pass 1), otherwise that record with the selected cube and its PAD_FRAME placement
        in place of its own (the same frame, hence the same pad homography). None only when the view produced no record at
        all, which the unchanged SEARCH check refuses as it always did. Anything else is a selection or a refusal before
        anything is picked up; a scene that shows only the placed cube is a refusal, never DONE_ALREADY_PLACED."""
        records = search.get("perception") or []
        if not records:
            return None
        record = records[-1]
        listed, code, cause = self._mission_candidates(record)
        if code is not None:
            raise self.failed(code, "SEARCH: the cubes of this frame could not be listed completely, so no cube can be told to be this target's; nothing was picked up",
                              evidence=record, enumeration_refusal=cause)
        frame, pan = self._pad_frame({"perception": [record]}), float(measured["shoulder_pan"])
        portfolio, decisions = self._search_portfolio, None  # the portfolio exists under an additive word only (_placement_policies_at_search)
        try:
            if portfolio is None:
                cubes = [self._mission_seen_cube(entry, frame, pan) for entry in listed]
            else:
                decided = [self._mission_seen_decision(entry, frame, pan, portfolio) for entry in listed]
                cubes, decisions = [cube for cube, _decision in decided], [decision for _cube, decision in decided]
        except (TypeError, ValueError):
            raise self.failed("mission_candidate_unreadable", "SEARCH: a listed cube does not carry a position this cycle can read; nothing was picked up", evidence=record)
        rules, set_aside, tiered = self._mission_rules(), 0, 0
        if portfolio is not None:  # THE BINDING TIERS, then THE PICKUP BANDS (block_selection), under the additive words only
            bands = [self._mission_pickup_band(entry, record, measured) if cube.mode is not None else None for entry, cube in zip(listed, cubes)]
            cubes, tiered, set_aside = block_selection.past_the_tiers_and_the_bands(cubes, decisions, bands, rules)
        multi_block = decisions is not None and block_selection.WORD in PLACEMENT_ENVELOPE
        try:
            if multi_block:
                bindings, selection = block_selection.select_for_execution(self.target, cubes, decisions, rules)
            else:
                bindings, selection = target_binding.select_for_execution(self.target, cubes, rules)
        except target_binding.TargetRefused as refusal:
            raise self.failed(refusal.reason_code, str(refusal), evidence=record, **refusal.values)
        entry = listed[selection.index]
        chosen = record if entry.get("pass") == 1 else {**record, "cube": entry["cube"], "cube_by_template": entry["cube_by_template"]}
        self._mission.update(bindings=bindings, selection=selection, search_observation_id=record.get("observation_id"))
        # candidates: the blocks off the pad this view listed; on_pad_ignored: the blocks on it, which decide nothing (the pad policy)
        self.state("TARGET_SELECTED", target_id=self.target.target_id, mode=selection.mode, seen_pad_m=[round(v, 4) for v in selection.seen_pad_m],
                   bound_pad_m=[round(v, 4) for v in selection.bound_pad_m], distance_to_binding_mm=round(selection.distance_mm, 1), cubes_listed=len(listed),
                   listed_pass=entry.get("pass"), targets_bound=None if bindings is None else len(bindings),
                   candidates=sum(1 for cube in cubes if target_binding.is_candidate(cube, rules)), on_pad_ignored=sum(1 for cube in cubes if target_binding.is_placed(cube, rules)),
                   **self._placement_decision_fields(decisions, selection, cubes, rules, multi_block, set_aside, tiered), evidence=record)
        return chosen

    @staticmethod
    def _placement_decision_fields(decisions, selection, cubes, rules, multi_block: bool, set_aside: int = 0, tiered: int = 0) -> dict:
        """TARGET_SELECTED's fields under an additive word, none otherwise (so the proven record gains no key): the chosen block's
        SEARCH decision (placement_selector.Decision.record: the policy, its plan, whether this build executes it, every
        policy's plan), the binding rule that chose the block (`multi_block`: the word binds by block_selection's THE
        RULE, which takes the installed controller's nearest the pad among the blocks the proven policy places and the least
        motion among the others; without it the nearest the pad, target_binding's), the candidates with a placement the
        binding read, how many it set aside because SEARCH's pickup bands refuse them (block_selection THE PICKUP BANDS AT
        THE BINDING), and the chosen block's tier and how many candidates the tiers set aside (block_selection THE BINDING
        TIERS)."""
        if decisions is None:
            return {}
        decision = decisions[selection.index]
        fields = {} if decision is None else decision.record()
        feasible = sum(1 for cube in cubes if target_binding.is_candidate(cube, rules) and cube.mode is not None)
        chose = decision is not None and decision.selected is not None
        rule = (block_selection.binding_rule(decision) if chose else "least_motion") if multi_block else "nearest_the_pad"
        return {**fields, "binding_rule": rule, "feasible_candidates": feasible, "band_refused_candidates": int(set_aside),
                "binding_tier": block_selection.tier_name(decision) if chose else None, "tier_set_aside_candidates": int(tiered)}

    def _mission_pickup_band(self, entry: dict, record: dict, measured: dict):
        """THE PICKUP BANDS AT THE BINDING (block_selection), under the additive words only: the code SEARCH's pickup bands
        would refuse this listed cube with once it is bound, in SEARCH's order (the return pan bound, the pregrasp entry bound
        from the pan the arm stands at, the near block band: policy_search.refuse_pregrasp_beyond_pan_bounds and policy_align.
        refuse_pickup_band, read here without stating anything), from the coarse pregrasp SEARCH computes for it
        (policy_align.coarse_pregrasp); None when they pass it. SEARCH takes the entry bound from the pan of the frame it
        decided on, and after a scene scan (scene_search) from the saved low pose the arm stands at, where it never refuses
        what the return bound passes."""
        seen = {**record, "cube": entry.get("cube"), "cube_by_template": entry.get("cube_by_template")}
        coarse = policy_align.coarse_pregrasp(self, _CONTROLLER_VIEW, seen, measured)
        home = self._home_pan_deg()
        stands = home if policy_search.SCENE_SEARCH_WORD in policy_search.POLICY.run_words(_CONTROLLER_VIEW) else float(measured["shoulder_pan"])
        if abs(float(coarse["pan_deg"]) - home) > RETURN_PAN_BOUND_DEG:
            return "search_cube_beyond_return_bound"
        if abs(float(coarse["pan_deg"]) - stands) > MAX_ENTRY_PAN_SWEEP_DEG:
            return "search_cube_beyond_pregrasp_entry_bound"
        if float(coarse["cube_out_mm"]) < policy_align.pickup_band_min_cube_out_mm(_CONTROLLER_VIEW):
            return "search_cube_inside_pregrasp_reach"
        return None

    def _mission_capability_gate(self, seen: dict) -> None:
        """SEARCH, after the selection was written back, for a cube that MEASURES BASE_ADVANCE (the module docstring,
        CAPABILITY). Nothing has moved; both refusals are taken before PREGRASP."""
        if not self.target.carries_capability:
            raise self.failed("mission_capability_required", "SEARCH: this target's cube measures BASE_ADVANCE, which needs the mobile_approach capability, and this run "
                              "carries none; nothing was picked up", evidence=seen, declared_mode=self.target.mode, measured_mode=target_binding.MODE_BASE_ADVANCE)
        source = _motion_module("target_observation_source")
        available, reason = source.capability_observation_available()
        if not available:
            raise self.failed("mission_capability_observation_unavailable", f"SEARCH: no actual observation of this target exists for the capability ({reason}); "
                              "nothing was picked up", evidence=seen, observation_reason=reason)

    def _mission_fallback_capability_gate(self, seen: dict) -> None:
        """SEARCH, after the executor guard and before anything moves, under an additive placement word only (THE ADDITIVE
        POLICIES in the module docstring; placement_executor, THE CAPABILITY OF AN ARM-ALONE BINDING and THE PROVEN TIE): an
        AUTO target bound to the arm alone, in a run that carries no capability, whose held cube an additive policy places
        (the additive policy SEARCH chose, or the one the executor hands the cube to once held where the proven arm-alone plan
        SEARCH chose needs the base at SEARCH's planning grasp, placement_executor.handed_at_search) and whose in-run fallback
        then moves the base. The runtime gives the capability only before a spawn, so this run asks for it here and nothing is
        picked up. Nothing without a portfolio, for a declared target or a run that carries the capability, for a plan that
        moves the base, for a proven arm-alone plan that keeps the cube, or when no fallback moves the base. The sentence
        states the fallback as what it is (verify r6 policy MF2): it runs only if THE WAY BACK UNDER LOAD brings the loaded
        arm to the carry pose, which holds it where it stands otherwise, and its advance is SEARCH's plan, which the run
        plans again from where it then stands."""
        portfolio = self._search_portfolio
        if portfolio is None or self.target is None or not self.target.auto or self.target.carries_capability:
            return
        decision, situation = portfolio.last, portfolio.last_situation
        if decision is None or decision.selected is None or situation is None:
            return
        chosen, policies = decision.selected, placement_selector.enabled(PLACEMENT_ENVELOPE)
        if chosen.mode != target_binding.MODE_ARM_ONLY:
            return
        tie = chosen.policy == placement_selector.PROVEN
        handed = placement_executor.handed_at_search(_CONTROLLER_VIEW, situation, policies, portfolio.proven) if tie else chosen.policy
        if handed is None:
            return
        fallback = placement_executor.fallback_at_search(_CONTROLLER_VIEW, situation, policies, handed)
        if fallback is None:
            return
        placed_by = f"proven, and once held {handed} when the proven plan needs the base from the measured grasp" if tie else chosen.policy
        raise self.failed("mission_fallback_capability_required", f"SEARCH: this target's block is placed by the arm alone ({placed_by}); should its release be refused, "
                          f"the same run goes on with {fallback.policy}, which moves the base with the mobile_approach capability, only if the loaded arm comes back to "
                          "the carry pose, otherwise the arm holds the cube where it stands for the operator; that advance, "
                          f"{fallback.base_advance_mm:.0f} mm, is planned now from SEARCH and planned again from where the run then stands; this run does not carry the "
                          "capability, and nothing was picked up", evidence=seen, declared_mode=self.target.mode, measured_mode=chosen.mode, placement_policy=chosen.policy,
                          handed_policy=handed, fallback_policy=fallback.policy, fallback_base_advance_mm=fallback.base_advance_mm)

    def observed_target(self):
        """What the capability relay publishes for this target (capability_pulse's required `observe`): the ACTUAL v2
        observation target_observation_source.resolve packages from the approach state advance_base keeps, the latest
        stamped capture (with its identity: the stored frame's digest and the desktop perception request that answered
        for it, when there was one), this run's selection, this clock and the perception budget. It raises
        ObservationUnavailable, naming every reason, whenever any of those is missing. Nothing here substitutes a
        coordinate: x_m is base_advance_needed_mm of the view the loop pulses on, and nothing else."""
        source = _motion_module("target_observation_source")
        selection = (self._mission or {}).get("selection") if self.target is not None else None
        return source.resolve(self._approach, self._capture_with_identity(), selection, time.monotonic_ns(),
                              perception_deadline_s=self._perception_deadline_s(), target_id=None if self.target is None else self.target.target_id)

    def _perception_deadline_s(self):
        """The desktop perception's deadline in seconds (remote_perception.RemotePerception.deadline_ns, the one budget a
        LIVE run was given), or None when perception runs here: then no observation has a freshness bound, and resolve
        refuses (perception_budget_unknown) rather than take one from anywhere else."""
        deadline_ns = getattr(self.perception, "deadline_ns", None)
        return None if deadline_ns is None else float(deadline_ns) / 1e9

    def _capture_with_identity(self):
        """The latest stamped capture with its identity filled in once, and cached on it: run_id (this run's scan id, the
        observation session), frame_sha256 (the stored PNG's bytes, cheap) and perception_request_id (the request_id of the
        exchange remote_perception wrote beside the record, <observation_id>-remote.json, when it was accepted). Each stays
        None when absent or unreadable; none of them is on the wire (target_observation_source, evidence)."""
        capture = self._capture
        if not isinstance(capture, dict) or "frame_sha256" in capture:
            return capture
        identity = {"run_id": self.scan_id, "frame_sha256": None, "perception_request_id": None}
        try:
            if capture.get("raw_frame_path"):
                identity["frame_sha256"] = hashlib.sha256((self.session_dir / capture["raw_frame_path"]).read_bytes()).hexdigest()
        except OSError:
            pass
        try:
            exchange = json.loads((self.session_dir / "perception" / f"{capture['observation_id']}-remote.json").read_text())
            if exchange.get("accepted") is True:
                identity["perception_request_id"] = ((exchange.get("request") or {}).get("request_id")) or None
        except (OSError, ValueError, TypeError, KeyError):
            pass
        capture.update(identity)
        return capture

    def _write_bound_result(self) -> None:
        """The selection (and, on the first execution, every binding) written back BEFORE the first motion: the result
        file's 'bound' stage. A run that cannot record what it is about to pick up picks up nothing."""
        mission = self._mission
        try:
            target_binding.write_result(self.target, target_binding.result_document(self.target, stage=target_binding.STAGE_BOUND, bindings=mission["bindings"],
                                                                                    selection=mission["selection"], search_observation_id=mission["search_observation_id"]))
        except (OSError, target_binding.MissionFileRefused) as problem:
            raise self.failed("mission_result_not_written", f"SEARCH: the selection could not be written back for the runtime ({getattr(problem, 'reason_code', type(problem).__name__)}); "
                              "nothing was picked up")

    def _mission_one_cube_in_view(self, cube: dict, view: dict, attempt: int) -> None:
        """ALIGN for a mission target: the pregrasp view must verify exactly ONE cube. The recognizer keeps its best verified
        cube and only counts the others (candidates_verified): with two in view, which one the jaws would close on is not a
        measurement. A view that does not record the count is refused as well; uniqueness is never assumed."""
        verified = (cube.get("cube") or {}).get("candidates_verified")
        counted = isinstance(verified, int) and not isinstance(verified, bool)
        if counted and verified == 1:
            return
        if counted and verified > 1:
            raise self.failed("align_more_than_one_cube_verified", f"ALIGN: the recognizer verified {verified} cubes in the pregrasp view; a mission target is approached only when its cube "
                              "is the one cube in view", evidence=view, attempt=attempt, candidates_verified=verified)
        raise self.failed("align_cube_count_not_recorded", "ALIGN: the pregrasp view does not record ONE verified cube; a mission target is approached only when its cube is the one cube in view",
                          evidence=view, attempt=attempt)

    def _mission_placed(self, final: dict, release, release_uncertainty_mm: float = 0.0) -> tuple:
        """PLACED for a mission target, from the same EXIT view the legacy predicate reads: (the attributed cube's placement,
        its centre-to-edge margin, placed). target_binding.attribute_placement credits the block on the pad nearest `release`
        (release_segment_pad of this run's RELEASE_ADMISSION) within RELEASE_AGREEMENT_MM plus `release_uncertainty_mm`,
        whatever was there before (ruling (ii)), and ignores every other block on the pad (THE RELEASE AGREEMENT). Nothing is
        attributed from a partial list."""
        records = final.get("perception") or []
        record = records[-1] if records else None
        listed, code, placement = [], "mission_placed_no_exit_view", None
        if record is not None:
            listed, code, _cause = self._mission_candidates(record)
        if code is None:
            try:
                cubes = [self._mission_seen_cube(entry, None, None) for entry in listed]
            except (TypeError, ValueError):
                code = "mission_candidate_unreadable"
            else:
                placement = target_binding.attribute_placement(cubes, release, self._mission_rules(), release_uncertainty_mm)
        if placement is None:  # nothing could be attributed: no distance and no tolerance are claimed
            placement = target_binding.Placement(False, None, None, 0, None, None, code)
        self._mission.update(placement=placement, exit_observation_id=None if record is None else record.get("observation_id"))
        if not placement.placed:
            return {}, float("nan"), False
        return dict(listed[placement.placed_index].get("cube_by_template") or {}), placed_margin_mm(placement.placed_pad_m), True

    def _mission_placed_fields(self) -> dict:
        """The attribution, beside the PLACED state's own fields. Nothing at all in the legacy cycle."""
        if self.target is None:
            return {}
        placement = self._mission["placement"]
        return {"target_id": self.target.target_id, "attributed": placement.placed, "on_pad_cubes": placement.on_pad_count,
                "release_distance_mm": None if placement.release_distance_mm is None else round(placement.release_distance_mm, 1),
                "release_tolerance_mm": placement.release_tolerance_mm, "attribution_reason": placement.reason_code}

    def _mission_predicates(self) -> dict:
        """The three placement predicates as far as this run EVALUATED them, None for one it never reached. A base that never
        advanced is where it started once PLACED is reached, which is what the legacy summary says as well."""
        summary, reached = self._mission["summary"] or {}, {entry["state"] for entry in self.transitions}
        advanced = bool(reached & {"BASE_ADVANCE", "BASE_MEASURED"})
        return {"object_secured": summary.get("object_secured") if "OBJECT_SECURED" in reached else None,
                "placed": summary.get("placed") if "PLACED" in reached else None,
                "base_returned": summary.get("base_returned") if "BASE_RETURNED" in reached else (True if "PLACED" in reached and not advanced else None)}

    def _capability_evidence(self):
        """Ids and numbers of what the capability relay did in this run (target_binding's evidence block): the generation and
        pid its first pulse read, the commands it accepted (submitted to the gateway), the ones the gateway APPLIED (sent to
        the base: the count a placement is credited on) and the ones it dropped, by reason. None for a target that does not
        require the capability, and when no pulse got as far as reading the capability's slot."""
        if not self.target.carries_capability:
            return None
        pulses = [pulse for pulse in (getattr(getattr(self.move_base, "capability", None), "pulses", None) or []) if isinstance(pulse, dict) and pulse.get("slot_generation") is not None]
        if not pulses:
            return None
        dropped = {}
        for pulse in pulses:
            for reason, count in (pulse.get("commands_dropped") or {}).items():
                dropped[_reason(reason)] = dropped.get(_reason(reason), 0) + int(count)
        return {"generation": int(pulses[0]["slot_generation"]), "pid": int(pulses[0]["producer_pid"]), "accepted": sum(int(pulse.get("commands_accepted") or 0) for pulse in pulses),
                "applied": sum(int(pulse.get("commands_applied") or 0) for pulse in pulses), "dropped": dropped}

    def mission_finished(self, status: int, error=None) -> int:
        """The mission result's 'finished' stage, echoing mission_id, target_id and run_id; then `status`, unchanged. The
        legacy cycle returns at once. It never raises and never changes the exit status: a result that cannot be written
        leaves the runtime without a placement claim for this run, which is the fail-closed reading of it."""
        if self.target is None:
            return status
        try:
            mission, predicates = self._mission, self._mission_predicates()
            # A stop the runtime asked for (the flag) makes a later failure "stopped", as a stop observed by the events does.
            outcome = _terminal_outcome(status, False, predicates, "mission_stop" if self.abort["set"] else None)
            reason = None if outcome == "placed_new" else mission["reason_code"]
            if reason is None and status == 4:  # an executor abort: its own sentence is the only thing that names it
                text = str(error or "")
                text = text[len("step aborted: "):] if text.startswith("step aborted: ") else text
                reason = next((code for start, code in _ABORT_CODES if text.startswith(start)), "aborted")
            elif reason is None and status == 3:
                reason = "refused_before_motion"
            result = target_binding.write_result(self.target, target_binding.result_document(
                self.target, stage=target_binding.STAGE_FINISHED, bindings=mission["bindings"], selection=mission["selection"], search_observation_id=mission["search_observation_id"],
                outcome=outcome, final_state=self.transitions[-1]["state"] if self.transitions else None, reason_code=None if reason is None else _reason(reason), predicates=predicates,
                placement=mission["placement"], exit_observation_id=mission["exit_observation_id"], capability=self._capability_evidence()))
            print(f"MISSION RESULT: target {self.target.target_id}: {result['outcome']}" + (f" ({result['reason_code']})" if result["reason_code"] else ""), flush=True)
        except Exception as problem:  # noqa: BLE001 -- the exit status is decided; nothing here may replace it
            print(f"MISSION RESULT NOT WRITTEN ({getattr(problem, 'reason_code', type(problem).__name__)}): the runtime reads no placement from this run", flush=True)
        return status

    def run_cycle(self) -> dict:
        summary = {"object_secured": False, "placed": False, "clearance_target_met": False, "footprint_clearance_mm": None, "base_advance_mm": 0.0, "base_returned": True}
        measured = self.pose()
        if tool_state(measured)["lowest_link_m"] < 0.10 or gripper_refusal(measured, False):
            self.events.start_pose_refused(measured)
            raise self.failed("start_pose_not_low_scan", "start from the low scan pose with the gripper acquired in its normal-open region",
                              lowest_link_m=tool_state(measured)["lowest_link_m"], gripper=measured["gripper"], gripper_open=GRIPPER_OPEN, gripper_open_tolerance=GRIPPER_OPEN_TOLERANCE)
        if self.target is not None:
            self._mission["summary"] = summary  # the same dict, filled in below: the result file reads the predicates from it
        if PLACEMENT_ENVELOPE:  # under a word only: the default record and stdout are byte for byte the proven ones
            self.state("PLACEMENT_ENVELOPE", words=list(PLACEMENT_ENVELOPE), attended_label=PLACEMENT_ENVELOPE_ATTENDED, qualified=sorted(envelope_words_qualified()))
            summary["placement_envelope"] = {"words": list(PLACEMENT_ENVELOPE), "attended_label": PLACEMENT_ENVELOPE_ATTENDED}
        # The five stages, each a policy module handed this Cycle (ctx) and this module's namespace (_CONTROLLER_VIEW); the
        # module docstring of autonomy/manipulation/stage_policy.py says why a stage never imports this file.
        with _placement_policies_at_search(self):  # under an additive word only: SEARCH plans with every policy the run names
            found = policy_search.run_search(self, _CONTROLLER_VIEW, summary, measured)  # SEARCH, SEARCH_SWEEP, the selection, every refusal before PREGRASP
        if found.done is not None:
            return found.done  # DONE_ALREADY_PLACED: nothing is picked up
        placement_selector.refuse_without_executor(self, self._search_portfolio, found.seen)  # a least-motion plan this build does not execute: before anything moves
        self._mission_fallback_capability_gate(found.seen)  # an arm-alone binding whose fallback moves the base asks for the capability first: before anything moves
        if policy_align_left_clearance.engaged(PLACEMENT_ENVELOPE) and additive_words_apply(self.target):  # the ALIGN word align_left_clearance, for a run it applies to
            aligned = policy_align_left_clearance.run_pregrasp_align(self, _CONTROLLER_VIEW, found.measured, found.coarse, found.seen, found.start_inside)
        else:
            aligned = policy_align.run_pregrasp_align(self, _CONTROLLER_VIEW, found.measured, found.coarse, found.seen, found.start_inside)  # PREGRASP and ALIGN
        held = policy_grasp.run_grasp(self, _CONTROLLER_VIEW, summary, aligned.view, aligned.decision)  # APPROACH to OBJECT_SECURED
        if placement_executor.engaged(PLACEMENT_ENVELOPE) and additive_words_apply(self.target):  # an additive placement word for a run it applies to: the policies it names place the held cube, the proven stage among them
            placement = placement_executor.place(self, _CONTROLLER_VIEW, summary, found.measured, found.seen, found.template, found.search_frame, held.view, held.third)
        else:
            placement = policy_placement.run_placement(self, _CONTROLLER_VIEW, summary, found.measured, found.seen, found.template, found.search_frame, held.view, held.third)  # PLACEMENT_PLAN to PLACED
        return policy_return.run_return(self, _CONTROLLER_VIEW, summary, **vars(placement))  # BASE_RETURN and the ending


# ---------------------------------------------------------------------------------------------------------------------
# EVENTS, part 2 of 2: the object that STATES what the code above did, as contract v1 records (packages/demo-contracts)
# handed to the non-blocking emitter of packages/demo-runtime. It is built only when KC_EVENTS_PATH is set.
#
# WHAT IT MAY DO: look at values the controller already holds, and ask the robot client for the STAMP of a read that has
# already happened. WHAT IT MAY NOT DO: command anything, read the robot, sleep, print to stdout, or raise. Every hook is
# wrapped (_hook): an error inside one is counted, stated once as an evidence gap and swallowed, so a defect here costs
# evidence and never a motion, an exit status, the disconnect or the cycle record.
#
# THE CONTRACT IS CLOSED (additionalProperties false throughout), so a fact goes where the contract has a place for it:
#   - a state's fields: state.transition carries machine / from_state / to_state and nothing else. The observations a
#     transition rests on travel in the envelope (observation_id, provenance.observation_ids); the numbers travel in the
#     predicate.evaluated records next to it; the [STATE] line itself travels as ONE diagnostic.text record, same text;
#   - the per-read stamps of a base-zero evaluation: predicate.evaluated has ONE consumed_source_stamp. Every base-velocity
#     SAMPLE a guard consumed is therefore stated once as observation.captured (source_role base, media scalar_set) with
#     its own source stamp, and the predicate lists one observation id PER READ, in read order. A sample that was served
#     twice appears twice, so the distinct ids are the distinct samples; the reads that were fresh are listed again;
#   - arrival errors and an abort reason: action.completed has a result and a duration only. The targets (action.planned)
#     and the measured joints (measured.state) carry the same action_id; an abort reason is a reason_code plus one
#     diagnostic.text record with the executor's own sentence.
# ---------------------------------------------------------------------------------------------------------------------
# What placement_plan and release_margin_worst_mm rest on beyond definitions: three numbers measured on two or three runs.
_PLACEMENT_EMPIRICAL = ("VISUAL_ANCHOR_UNCERTAINTY_MM", "FK_TRAVEL_OVERSTATEMENT", "ARM_LOADED_REACH_OUT_MM")
# Every constant above whose own comment says it was MEASURED on a few recorded runs (as opposed to defined, or physically
# validated as a bound). run.config marks them, so that a reader of a record knows which thresholds are thin evidence.
_EMPIRICAL_CONSTANTS = _PLACEMENT_EMPIRICAL + (
    "GRIPPER_HOLDING_BAND", "GRIPPER_EMPTY_LATCH_MARGIN", "FOCAL_PX", "CAMERA_TILT_BEYOND_TOOL_DEG", "NADIR_COLUMN_PX", "GRASP_REF_CENTER_PX", "GRASP_REF_WIDTH_PX", "CAPTURE_WINDOW_MM",
    "LOW_POSE_REF_CONTACT_PX", "LOW_POSE_REF_GRASP", "LOW_POSE_PX_PER_PAN_DEG", "LOW_POSE_PX_PER_RADIAL_MM", "APPROACH_REACH_LEAD_MM", "PREGRASP_OUTWARD_BIAS_MM", "RETENTION_MIN_SCORE",
    "PLACE_LEAD_MM", "CARRY_MIN_GAIN_MM", "LOADED_FORWARD_GAIN", "BASE_RATE_PRIOR_MM_S", "BASE_MAX_ARM_DRIFT_DEG", "BASE_POSTURE_TOLERANCE_DEG")
_ACTION_KINDS = ("observe", "pan", "move", "grasp", "grip", "base", "exit", "cycle", "scan")  # what --do dispatches on; anything else is reported as "unknown", never echoed
_RUN_PREDICATES = ("object_secured", "placed", "clearance_target_met", "base_returned")  # run.finished.predicates of the contract
# What a PLACEMENT is, which is not the same question as whether the placement was good enough. These three are the physical
# claim: the cube was held, it is on the pad, and the base came back. clearance_target_met is a measured QUALITY beside them
# and is reported as its own predicate, never folded into the word: a run that placed the cube short of the footprint target
# placed the cube, and `unknown` is reserved by the contract for a run nothing established. Full acceptance is all four, and
# it is judged where acceptance belongs, by the reader holding all four - not here, where the only question is what happened.
_PLACEMENT_PREDICATES = ("object_secured", "placed", "base_returned")


def _terminal_outcome(exit_status, already_placed: bool, predicates: dict, stop_path: bool) -> str:
    """The ONE place a run gets its terminal word, in the precedence the controller has always had: a cube already on the
    pad commanded nothing and is never a placement; a clean exit is judged on what was MEASURED; and an exit that already
    names how the run ended never consults a predicate. Every word it can return is defined by event.schema.json's
    run_outcome, and `unknown` keeps its contract meaning - nothing established what happened - so it is said only when a
    physical predicate was false or was never evaluated."""
    if exit_status == 0 and already_placed:
        return "already_placed_noop"
    if exit_status == 0:
        return "placed_new" if all(predicates[name] is True for name in _PLACEMENT_PREDICATES) else "unknown"
    # the controller's documented exit statuses: 3 refused, 4 stopped, 5 failed. A failure that FOLLOWED an observed stop is the stop.
    return {3: "refused", 4: "stopped", 5: "stopped" if stop_path else "failed"}.get(exit_status, "failed")


# What a predicate's provenance names. The cube box and the pad candidate come from the detector MODEL; a position in PAD_FRAME
# rests on the pad TEMPLATE. The retention score, the tool height, the gripper readback and the base velocity rest on neither.
_MODEL_PREDICATES = frozenset({"already_placed", "aligned", "placement_plan", "arm_only_sufficient", "pad_frame_valid_while_holding", "release_margin", "placed", "clearance_target_met"})
_TEMPLATE_PREDICATES = frozenset({"already_placed", "placement_plan", "arm_only_sufficient", "pad_frame_valid_while_holding", "base_measured", "release_margin", "placed", "clearance_target_met", "base_returned"})
_CONSTANT_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_UNIT_BY_SUFFIX = (("_MM_S", "mm/s"), ("_MPS", "m/s"), ("_FPS", "Hz"), ("_MM", "mm"), ("_DEG", "deg"), ("_PX", "px"), ("_M", "m"), ("_S", "s"))
_NOT_NAMED_TEXT = re.compile(r"[^A-Za-z0-9_.+ -]")  # outside common#/$defs/named_value.value
_NOT_ID = re.compile(r"[^A-Za-z0-9_.-]")  # outside common#/$defs/id
_NOT_REASON = re.compile(r"[^a-z0-9_.-]")  # outside common#/$defs/reason_code, which is lower case
_DOTTED_QUAD = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_PATH_LIKE = re.compile(r"(?<![\w.])(?:~|/)(?:[\w.+@-]+/)+[\w.+@-]*")  # an absolute or home-relative path of two or more components
_SAFE_TEXT_TRIPWIRE = re.compile(r"/(home|Users|root)/|(^|[^0-9.])[0-9]{1,3}(\.[0-9]{1,3}){3}([^0-9.]|$)")  # common#/$defs/safe_text.not, mirrored: what would fail the record
# scan_world's own abort sentences, by how they begin. The executor has no codes and may not be edited, so its literal
# text is the only thing to go by; anything it says that is not listed here is "aborted", never a guess.
_ABORT_CODES = (("operator abort", "operator_abort"), ("base velocity not confirmed zero before", "base_not_zero_before_segment"), ("base velocity not confirmed zero after", "base_not_zero_after_segment"),
                ("base velocity was not zero while", "base_not_zero_at_observation"), ("observation could not be stored", "observation_not_stored"))


def _number(value):
    """A finite float, or None. NaN and the infinities are not JSON and the emitter refuses the whole record that holds one."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _flag(value):
    return None if value is None else bool(value)


def _id_text(text, fallback: str = "unnamed") -> str:
    return _NOT_ID.sub("_", str(text))[:64] or fallback


def _reason(text, fallback: str = "failed") -> str:
    """One reason_code of the contract: lower case, and it must START with a letter or a digit. The codes this module
    mints already have that shape; this only guarantees it, so that a record can never be invalid because of a code."""
    code = _NOT_REASON.sub("_", str(text).lower())[:64].lstrip("_.-")
    return code or fallback


def _named(name, value, unit=None) -> dict:
    """One named_value of the contract. A text value keeps only the characters the contract allows there (it cannot spell a
    path or a URL) and is withheld whole when it still reads as a dotted quad."""
    if value is None or isinstance(value, bool):
        shown = value
    elif isinstance(value, int):
        shown = value
    elif isinstance(value, float) or hasattr(value, "__float__"):
        shown = _number(value)
    else:
        shown = _NOT_NAMED_TEXT.sub("", str(value))[:96]
        shown = None if _DOTTED_QUAD.search(shown) else shown
    return {"name": _id_text(name), "value": shown, "unit": unit}


def _unit_of(name: str):
    """The unit a constant's NAME declares by this module's naming convention (STEP_MAX_DZ_M, BASE_PULSE_S, ...). A ratio
    names both sides (LOW_POSE_PX_PER_PAN_DEG is px/deg). None when the name declares nothing: a missing unit is honest."""
    upper = name.upper()
    if "_PER_" in upper:
        top, bottom = (_unit_of("_" + part.rsplit("_", 1)[-1]) for part in upper.split("_PER_", 1))
        return f"{top}/{bottom}" if top and bottom else None
    return next((unit for suffix, unit in _UNIT_BY_SUFFIX if upper.endswith(suffix)), None)


def effective_constants() -> list:
    """Every decision constant this run REALLY used, by name, value and unit: the module globals as they are now, plus the
    few of the executor (scan_world) and of the scan CLI that its decisions rest on. Read from the live modules, so nobody
    has to parse this file's source to know a threshold, and a constant that was changed shows its changed value."""
    out = []
    for prefix, namespace in (("", globals()), ("scan_world.", vars(sys.modules[run_scan_world.__module__])), ("scan_cli.", vars(cli))):
        for name in sorted(namespace):
            value = namespace[name]
            if not _CONSTANT_NAME.fullmatch(name):
                continue
            if isinstance(value, dict):
                parts = [(f"{name}.{key}", item) for key, item in value.items() if isinstance(key, str)]
            elif isinstance(value, (tuple, list)):
                parts = [(f"{name}.{index}", item) for index, item in enumerate(value)]
            else:
                parts = [(name, value)]
            for part, item in parts:
                if isinstance(item, bool) or (isinstance(item, (int, float)) and math.isfinite(item)):
                    out.append(_named(f"constant.{prefix}{part.lower()}", item, _unit_of(part) or _unit_of(name)))  # an element of BASE_PULSE_S is in seconds because its container says so
    return out + [_named(f"empirical.{name.lower()}", True) for name in _EMPIRICAL_CONSTANTS]


def stage_policy_summary(environ=None) -> list:
    """run.config's policy.<stage>.selection and policy.<stage>.digest for the five stages (stage_policy, THE POLICY
    IDENTITY), then policy.<stage>.<word>.selection and .digest for each additive policy (_ADDITIVE_POLICIES) whose word the
    run names, and for no other. run.config is stated before the words are resolved, so they are read here from the
    environment exactly as _run reads them; words that _run will refuse give a selection of null. The digest is the hex of
    the module's sha256 (a named value cannot carry the "sha256:" of rule_digest)."""
    try:
        words = placement_envelope_words(environ)
    except ScanRefused:
        words = None
    out = []
    for policy in _STAGE_POLICIES:
        stage_words = None if words is None else policy.run_words(stage_policy.ControllerView({"PLACEMENT_ENVELOPE": words}))
        out.append(_named(f"policy.{policy.stage}.selection", None if stage_words is None else stage_policy.selection_of(stage_words)))
        out.append(_named(f"policy.{policy.stage}.digest", policy.digest.split(":", 1)[1]))
    for policy in _ADDITIVE_POLICIES:  # an additive policy only when the run names its word: a run with none records what it did before them
        named = () if words is None else policy.run_words(stage_policy.ControllerView({"PLACEMENT_ENVELOPE": words}))
        if named:
            out.append(_named(f"policy.{policy.stage}.{policy.words[0]}.selection", stage_policy.selection_of(named)))
            out.append(_named(f"policy.{policy.stage}.{policy.words[0]}.digest", policy.digest.split(":", 1)[1]))
    return out


def _hook(method):
    """A hook may never change what the controller does. Whatever goes wrong inside one is counted, stated once and swallowed."""
    @functools.wraps(method)
    def safe(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:  # noqa: BLE001 -- KeyboardInterrupt and SystemExit are not Exceptions and pass, on purpose
            self._hook_failed(method.__name__)
            return None
    return safe


class _WatchedReads:
    """get_observation() of the robot, handed back UNCHANGED; what was served, and the stamp of that exact read, go to the
    events. READS only: every command keeps going to the robot object itself, which this class never touches."""

    def __init__(self, robot, events):
        self._robot, self._events = robot, events

    def get_observation(self):
        observation = self._robot.get_observation()
        self._events.consumed(self._robot, observation)
        return observation


class CycleEvents(_NoEvents):
    enabled = True

    def __init__(self, emitter, private_tokens=()):
        self.emitter = emitter
        self._tokens = sorted({token for token in private_tokens if isinstance(token, str) and len(token) >= 4}, key=len, reverse=True)
        self.hook_errors, self._hooks_failed, self._gaps_stated = 0, set(), set()
        self._config_digest = self._calibration_digest = self._model_identity = None
        self._action, self._actions, self._moved = None, 0, False
        self._state = None
        self._read, self._reads, self._group, self._polling, self._reading_base = None, 0, None, False, False
        self._samples = set()  # base-velocity samples already stated as observation.captured
        self._unstamped = 0  # how many unstamped base reads have been NAMED, so that every one of them gets its own name
        self._known = {}  # id(object) -> (object, observation id). The object is kept so that its id() cannot be handed out again.
        self._stamped = {}  # id(measured pose dict) -> (dict, source stamp): the stamp of the read that PRODUCED that dict
        self._gripper_read = None  # (gripper value, source stamp) of the read grip() took after the hold
        self._pad_measurements = {True: [], False: []}  # observation ids of the pad frame pairs behind BASE_MEASURED, by "cube held"
        self.predicates = dict.fromkeys(_RUN_PREDICATES)
        self.retries = {"grasp_retry_advance": 0, "pan_trim": 0}
        self._already_placed, self._stop_path, self._stop_requested, self._abort = False, None, False, None
        self._lazy_model, self._finished = False, False
        # Why the run did not end well. _failure is what a RAISE SITE of the controller decided and always wins; _last_ending
        # is the fallback for the endings this module does not raise itself (the executor's abort, a refusal before any
        # action existed), where the only thing that names the cause is the sentence that executor produced.
        self._failure, self._last_ending = None, None
        # THE POLICY IDENTITY (autonomy/manipulation/stage_policy.py): the identities of the stage procedures running now,
        # innermost last. A predicate the innermost stage that declares it decides carries that stage's rule_ref and rule_digest.
        self._policies = []
        self._capability_pulses_stated = 0  # how many of the capability relay's pulse records capability_output_applied has stated

    # ------------------------------------------------------------------ plumbing
    def _hook_failed(self, name: str) -> None:
        try:
            self.hook_errors += 1
            if name not in self._hooks_failed:
                self._hooks_failed.add(name)
                self.emitter.emit("evidence.gap", {"gap": "other", "detail": f"an event hook of the controller could not state its fact ({_id_text(name)}); the controller itself was not affected", "observation_ids": []},
                                  reason_code="event_hook_failed")
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, kind: str, payload: dict, **envelope):
        if self._action is not None and "action_id" not in envelope:
            envelope["action_id"] = self._action["id"]
        return self.emitter.emit(kind, payload, **envelope)

    def _gap_once(self, key: str, gap: str, detail: str, observation_ids=(), reason_code=None) -> None:
        if key not in self._gaps_stated:  # ONE gap per cause: run.finished counts gaps, and a cause that repeats on every read is still one thing that is missing
            self._gaps_stated.add(key)
            self._emit("evidence.gap", {"gap": gap, "detail": self._text(detail), "observation_ids": list(observation_ids)}, reason_code=reason_code or key)

    def _text(self, text) -> str:
        """Text for an operator's screen: this run's private values, anything shaped like a path and anything shaped like an
        address are taken out; what would still trip the contract's own tripwire is withheld whole."""
        text = str(text)
        for token in self._tokens:
            text = text.replace(token, "[redacted]")
        text = _DOTTED_QUAD.sub("[address]", _PATH_LIKE.sub("[path]", text))[:1024]
        return "[withheld]" if _SAFE_TEXT_TRIPWIRE.search(text) else text

    def _say(self, text, reason_code=None, level: str = "info", stream: str = "log") -> None:
        clean = self._text(text)
        self._emit("diagnostic.text", {"stream": stream, "level": level, "text": clean, "redacted": clean != str(text)[:1024]}, reason_code=reason_code)

    def _ids(self, *things) -> list:
        """The observation ids behind what the controller holds: a perception record names its own; a run_pose / observe
        result stands for the record its decisions use (the LAST one: _cube, _pad_frame and _frame all read records[-1]); a
        pad frame dict is known by identity from the moment it was perceived. Anything else has no id and gives none."""
        out = []
        for thing in things:
            if isinstance(thing, str):
                out.append(thing)
            elif isinstance(thing, (list, tuple)):
                out.extend(self._ids(*thing))
            elif isinstance(thing, dict):
                known = self._known.get(id(thing))
                if isinstance(thing.get("observation_id"), str):
                    out.append(thing["observation_id"])
                elif thing.get("perception"):
                    out.extend(self._ids(thing["perception"][-1]))
                elif known is not None and known[0] is thing:
                    out.append(known[1])
        return [item for item in out if not _NOT_ID.search(item) and 0 < len(item) <= 64]

    def _provenance(self, observation_ids, model: bool = False, calibration: bool = False) -> dict:
        return {"observation_ids": list(observation_ids), "model_identity": self._model_identity if model else None, "calibration_digest": self._calibration_digest if calibration else None,
                "config_digest": self._config_digest, "module_generation": None, "reconstructed": False}

    # ------------------------------------------------------------------ the run
    @_hook
    def run_started(self, args) -> None:
        # command_id and command_type are null: the contract says so for a run the console did not start, and this process
        # cannot know who started it. A supervisor that did start it has already said run.started and folds this one.
        self._emit("run.started", {"command_id": None, "command_type": None, "data_mode": "live", "plan_only": bool(args.plan_only), "holding": bool(args.holding)})

    @_hook
    def run_config(self, args, config, stamped_reads: bool, require_fresh: bool) -> None:
        runtime = cli.import_runtime("demo_runtime.config")
        kind = args.do.partition(":")[0]
        summary = [_named("controller", "single-cube-cycle"), _named("action_kind", kind if kind in _ACTION_KINDS else "unknown"), _named("plan_only", bool(args.plan_only)), _named("holding", bool(args.holding)),
                   _named("runtime_config_given", config is not None), _named("stamped_reads", bool(stamped_reads)), _named("freshness_refusal", bool(require_fresh)),
                   # THE PROVIDER THIS RUN ACTUALLY SELECTED, read from the argv this process was started with rather
                   # than from the configuration's digest. A digest states what a file said; this states which code
                   # opened the camera, and only the second one answers the question after the fact.
                   _named("capture_provider", args.capture_provider)]
        if config is None:  # no file was given: what this run effectively used IS the legacy layout, and that has a digest too
            effective = runtime.legacy_layout(ROOT, environ=os.environ, host_address=args.remote_ip, robot_id=args.robot_id, pi_ssh_target=args.pi_ssh or "")
        else:
            effective = config
            summary.append(_named("host_arguments_match_config", (args.remote_ip, args.robot_id) == (config.host.address, config.host.robot_id)))
        self._config_digest = runtime.config_digest(effective)
        for name, value in runtime.sanitized_summary(effective).items():
            if name not in ("schema", "config_digest") and (value is None or isinstance(value, (bool, int, float)) or not _NOT_NAMED_TEXT.search(str(value))):
                summary.append(_named(f"config.{name}", value))  # a text that does not fit a named value (a hub name holds a slash) is left out; the model travels in provenance
        try:  # the pad template is the calibration every PAD_FRAME of this run rests on; its digest is what provenance can say about it
            self._calibration_digest = cli.import_runtime("demo_runtime.preflight").input_digests(effective)["pad_template_sha256"]
        except Exception:  # noqa: BLE001
            self._gap_once("calibration_not_recorded", "calibration_not_recorded", "the pad template of this run could not be digested, so no record of this run can name the calibration it rests on")
        self._emit("run.config", {"config_digest": self._config_digest, "summary": summary + stage_policy_summary() + effective_constants()})

    @_hook
    def refused_before_connect(self, args, reason_code: str, detail: str) -> None:
        kind = args.do.partition(":")[0]
        self._note_ending(reason_code, detail)
        self._emit("action.refused", {"action_type": kind if kind in _ACTION_KINDS else "unknown", "before_motion": True, "detail": self._text(detail)}, action_id=self._next_action_id(), reason_code=reason_code)

    @_hook
    def refused(self, args, refusal) -> None:
        """main()'s 'REFUSED before any motion': a pose builder or an action refused. An action that was already stated as
        refused (run_pose, grip) is not stated twice; a refusal that came BEFORE any action existed gets its own record."""
        if getattr(refusal, "_kc_stated", False):
            return
        self.refused_before_connect(args, "refused_before_motion", refusal)

    @_hook
    def cycle_returned(self, summary: dict) -> None:
        # The base never moved: the controller's own summary says base_returned True, and the measured displacement is 0.
        if self.predicates["base_returned"] is None and summary.get("base_returned") is True and not summary.get("base_advance_mm") and not self._already_placed:
            self.predicate("base_returned", True, measured=0.0, threshold=BASE_RETURN_TOLERANCE_MM, unit="mm", scalars=("base_never_advanced",))

    @_hook
    def cycle_failed(self, reason_code: str, message, values: dict, evidence) -> None:
        """Cycle.failed(): the reason ONE raise site of the controller ended the run, as that site decided it. Stated here
        and kept for run.finished, which is the record a console reads; the sentence travels with it as the operator saw
        it, next to the values and the thresholds that decided it and the observations that were in scope."""
        self._failure = {"reason_code": _reason(reason_code), "detail": self._text(message),
                         "measurements": [_named(name, value, _unit_of(name)) for name, value in values.items()], "observation_ids": self._ids(evidence)}
        self._say(message, reason_code=self._failure["reason_code"], level="error")

    @_hook
    def _note_ending(self, reason_code, detail) -> None:
        """The fallback reason for an ending this module did not raise itself: an executor abort (its sentence is the only
        thing that names it, which _ABORT_CODES already reads) or a refusal from before any action existed. A raise site of
        the controller always wins over this, whichever came first."""
        self._last_ending = {"reason_code": _reason(reason_code), "detail": self._text(detail), "measurements": [], "observation_ids": []}

    def finish(self, exit_status, crashed: bool = False) -> None:
        """run.finished, then ONE bounded close. Called from main()'s outermost finally. It swallows everything, Exception
        or not: at this point the controller has decided its exit status and nothing here may replace it."""
        try:
            if self._finished:
                return
            self._finished = True
            self._close_base_group()
            self._stop_at_exit()
            predicates = dict(self.predicates)
            outcome = _terminal_outcome(exit_status, self._already_placed, predicates, self._stop_path)
            # WHY it ended that way, so that a console can say it without a terminal: what a raise site of the controller
            # decided, or else the last ending this process saw. A run that ended as it should carries none of it.
            failure = None if outcome in ("placed_new", "already_placed_noop") else (self._failure or self._last_ending)
            status = self.emitter.status()  # taken BEFORE run.finished is queued, as the emitter prescribes; close() below has the last word
            self.emitter.emit("run.finished", {"outcome": outcome, "exit_status": None if crashed else exit_status, "predicates": predicates,
                                               "automatic_retries": sum(self.retries.values()), "task_control_interventions": 1 if self._stop_requested else 0,
                                               "evidence_complete": bool(status["evidence_complete"]), "evidence_gaps": int(status["evidence_gaps"]),
                                               "reason_code": None if failure is None else failure["reason_code"], "phase": self._state,
                                               "failure": None if failure is None else {key: failure[key] for key in ("detail", "measurements", "observation_ids")}},
                               reason_code=None if failure is None else failure["reason_code"])
        except BaseException:  # noqa: BLE001
            pass
        try:
            self.emitter.close(2.0)
        except BaseException:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ reads and their stamps
    def reads(self, robot):
        return _WatchedReads(robot, self)

    @_hook
    def consumed(self, robot, observation) -> None:
        to_stamp, last = getattr(robot, "to_source_stamp", None), getattr(robot, "last_stamp", None)
        stamp, detail = (to_stamp(), last()) if callable(to_stamp) and callable(last) else (None, None)
        self._reads += 1
        self._read = {"observation": observation, "stamp": stamp, "detail": detail, "session": getattr(robot, "local_rx_session", None)}
        if detail is not None and detail.get("availability") == "host_does_not_stamp":
            self._gap_once("host_does_not_stamp", "source_stamp_missing", "the host does not stamp its observations: every consumed stamp of this run is receiver-side (the laptop's receive counter and receive time), not the host's sample identity")
        if not self._reading_base:
            self._close_base_group()  # a base-zero evaluation never reads anything but the base: any other read ends it
            return
        if self._group is not None and not self._polling:
            self._close_base_group()  # a base read that does not follow the guard's own poll sleep begins ANOTHER evaluation
        self._group = (self._group or []) + [self._read]
        self._polling = False

    def base_reads(self, read_base):
        def watched():
            self._reading_base = True
            try:
                return read_base()
            finally:
                self._reading_base = False
        return watched

    def polls(self, sleep):
        def watched(seconds):
            self._polled(seconds)
            return sleep(seconds)
        return watched

    @_hook
    def _polled(self, seconds) -> None:
        guard = sys.modules[run_scan_world.__module__]
        if self._group is not None and not self._polling and seconds == guard.BASE_ZERO_POLL_S:
            self._polling = True  # the pause BETWEEN two reads of one evaluation
        else:
            self._close_base_group()

    def _sample_id(self, read: dict) -> str:
        detail = read["detail"]
        if detail is None or not detail.get("local_rx_seq"):
            # No stamp: the READ is all that can be named, and the name must still be unique within the RUN. read["n"] is
            # not: the executor's reads are numbered from 1 for the whole run, while the reads a base primitive's own
            # client served are numbered from 0 for EACH pulse (they are cleared per pulse), so a later pulse re-used the
            # earlier pulse's names and its stop confirmation named the FIRST pulse's samples. Named once and remembered
            # on the read, so a read that is stated twice keeps its name instead of collecting a second one.
            if "unstamped" not in read:
                self._unstamped += 1
                read["unstamped"] = self._unstamped
            return f"base-read-{read['unstamped']:06d}"
        return _id_text(f"base-rx-{str(read['session'] or 'client')[-6:]}-{detail['local_rx_seq']:06d}")  # the SAMPLE: two reads of one sample share it

    def _state_samples(self, reads: list) -> tuple:
        """(one observation id per read, the ids of the fresh reads). A sample is stated once, with ITS stamp, when first used."""
        ids, fresh = [], []
        for read in reads:
            sample = self._sample_id(read)
            if sample not in self._samples:
                self._samples.add(sample)
                self._emit("observation.captured", {"source_role": "base", "media": "scalar_set", "width_px": None, "height_px": None, "content_digest": None, "source_stamp": read["stamp"], "retained": False},
                           observation_id=sample)
            ids.append(sample)
            if read["detail"] is not None and read["detail"].get("fresh"):
                fresh.append(sample)
        if reads and all(read["stamp"] is None for read in reads):
            self._gap_once("reads_not_stamped", "source_stamp_missing", "the robot client of this run does not report which sample a read consumed, so no read of this run carries a source stamp")
        return ids, fresh

    @_hook
    def _close_base_group(self) -> None:
        group, self._group, self._polling = self._group, None, False
        guard = sys.modules[run_scan_world.__module__]
        if not group or len(group) < guard.BASE_ZERO_CONSECUTIVE_READS:
            return  # the single base read scan_world takes WITH an observation: not an evaluation of the base-zero guard
        bases = [{key: float(read["observation"][key]) for key in cli.BASE_VELOCITY_KEYS} for read in group]
        replay = iter(bases)
        try:  # the value is the executor's OWN function over the reads it really took, not a copy of its rule
            value = bool(_base_is_zero(ScanWorldDeps(read_pose=None, read_base=lambda: next(replay), move_arm=None, capture=None, now_utc=None, sleep=_nothing)))
        except StopIteration:
            value = None
        zero_run = 0
        for base in reversed(bases):
            if not guard._reads_zero(base):
                break
            zero_run += 1
        ids, fresh = self._state_samples(group)
        self._emit("predicate.evaluated", {"predicate": "base_zero", "value": value, "threshold": float(guard.BASE_ZERO_CONSECUTIVE_READS), "measured": float(zero_run), "unit": "reads",
                                           "evidence": [{"type": "scalar", "observation_ids": ids, "scalar_name": "base_velocity_reads"}, {"type": "scalar", "observation_ids": fresh, "scalar_name": "base_velocity_fresh_reads"}],
                                           "consumed_source_stamp": group[-1]["stamp"]}, provenance=self._provenance(ids))

    @_hook
    def measured_state(self, measured: dict) -> None:
        """measured.state for the pose read that JUST happened. The record is built from the observation that read served, so
        joints, base velocity and stamp are one sample. If the last read is not the one that produced `measured`, nothing is
        stated: a stamp is never attached to a state it does not belong to."""
        read = self._read
        if read is None or any(float(read["observation"][f"arm_{name}.pos"]) != float(measured[name]) for name in cli.MEASURED_JOINT_KEYS):
            return self._gap_once("measured_state_unpaired", "other", "a measured state could not be paired with the read that produced it and was not stated")
        self._stamped[id(measured)] = (measured, read["stamp"])
        while len(self._stamped) > 256:  # a predicate looks back a few actions at most (PLACEMENT_PLAN, to the pose read behind the grasp radius)
            self._stamped.pop(next(iter(self._stamped)))
        if read["stamp"] is None:
            return self._gap_once("reads_not_stamped", "source_stamp_missing", "the robot client of this run does not report which sample a read consumed, so no read of this run carries a source stamp")
        observation = read["observation"]
        self._emit("measured.state", {"joints": {name: _number(observation.get(f"arm_{name}.pos")) for name in cli.MEASURED_JOINT_KEYS},
                                      "base_velocity": {"x_mps": _number(observation.get("x.vel")), "y_mps": _number(observation.get("y.vel")), "theta_dps": _number(observation.get("theta.vel"))},
                                      "consumed_source_stamp": read["stamp"]})

    def _stamp_behind(self, measured):
        known = self._stamped.get(id(measured)) if measured is not None else None
        return known[1] if known is not None and known[0] is measured else None

    # ------------------------------------------------------------------ states and predicates
    @_hook
    def state(self, name: str, fields: dict, evidence) -> None:
        ids = self._ids(evidence)
        previous, self._state = self._state, _id_text(name)
        if name == "DONE_ALREADY_PLACED":
            self._already_placed, self.predicates["placed"] = True, True
        self._emit("state.transition", {"machine": "cycle", "from_state": previous, "to_state": self._state}, observation_id=ids[-1] if ids else None, provenance=self._provenance(ids) if ids else None)
        self._say(f"[STATE] {name} {json.dumps(fields, default=float)}", stream="stdout")  # the line the operator saw: the fields have no other place in the contract

    @_hook
    def predicate(self, name: str, value, measured=None, threshold=None, unit=None, frames=None, frame_pair=None, pad_measurements=None, anchor=None, scalars=(), empirical=(), stamp_of=None, gripper_read=None,
                  measurements=None) -> None:
        evidence, every = [], []
        for kind, things in (("frame", frames), ("frame_pair", frame_pair), ("pad_measurement", pad_measurements), ("anchor", anchor)):
            ids = self._ids(things)
            if ids:
                evidence.append({"type": kind, "observation_ids": ids, "scalar_name": None})
                every += ids
        evidence += [{"type": "scalar", "observation_ids": [], "scalar_name": _id_text(scalar)} for scalar in scalars]
        evidence += [{"type": "scalar", "observation_ids": [], "scalar_name": _id_text(f"constant.{constant.lower()}.empirical")} for constant in empirical]
        stamp = self._stamp_behind(stamp_of)
        if gripper_read is not None and self._gripper_read is not None and self._gripper_read[0] == gripper_read:
            stamp = self._gripper_read[1]
        if name in self.predicates:
            self.predicates[name] = _flag(value)
        payload = {"predicate": _id_text(name), "value": _flag(value), "threshold": _number(threshold), "measured": _number(measured), "unit": unit, "evidence": evidence, "consumed_source_stamp": stamp}
        rule = next((policy for policy in reversed(self._policies) if name in policy["predicates"]), None)
        if rule is not None:  # the optional extension of the contract: the stage rule that decided this verdict, and the digest of its module
            payload["rule_ref"], payload["rule_digest"] = _id_text(rule["rule_ref"]), rule["rule_digest"]
        if measurements:  # the optional extension of the contract: the further numbers this decision consumed. Absent when there are none, so every predicate stated before is unchanged.
            payload["measurements"] = [_named(item, item_value, _unit_of(item)) for item, item_value in dict(measurements).items()]
        self._emit("predicate.evaluated", payload, observation_id=every[-1] if every else None,
                   provenance=self._provenance(every, model=name in _MODEL_PREDICATES, calibration=name in _TEMPLATE_PREDICATES))

    @_hook
    def placement_infeasible(self, anchor, direction, grasp_out_mm, seen, measured) -> None:
        """The pre-pickup twin of the placement_plan predicate: the same physical question, asked at SEARCH before anything
        is picked up, so that the ONE refusal that leaves the cube where it lies is not the ONE failure with no predicate.
        The best achievable margin is recomputed here, over the same two bounded searches placement_plan itself walked
        (the loaded arm reach, then the bounded base advance), so the record says what WAS achievable and not only that the
        requirement was not met. All of it happens inside the hook: with no events configured none of it runs."""
        reachable = [release_margin_worst_mm(anchor, direction, float(travel)) for travel in np.arange(0.0, max(ARM_LOADED_REACH_OUT_MM - grasp_out_mm, 0.0) + 0.5, 1.0)]
        advanced = [release_margin_worst_mm((float(anchor[0]) + direction[0] * advance / 1000.0, float(anchor[1]) + direction[1] * advance / 1000.0), direction, 0.0)
                    for advance in np.arange(0.0, BASE_MAX_TOTAL_ADVANCE_MM + 0.5, 1.0)]
        self.predicate("placement_plan", False, measured=max(reachable + advanced), threshold=PLACE_REQUIRED_CENTRE_MM, unit="mm", anchor=seen,
                       scalars=("anchor_margin_mm", "grasp_out_mm", "best_achievable_centre_margin_mm", "constant.base_max_total_advance_mm"), empirical=_PLACEMENT_EMPIRICAL, stamp_of=measured,
                       measurements={"anchor_pad_x_m": anchor[0], "anchor_pad_y_m": anchor[1], "anchor_margin_mm": placed_margin_mm(anchor), "reach_direction_pad_x": direction[0],
                                     "reach_direction_pad_y": direction[1], "grasp_out_mm": grasp_out_mm, "best_with_the_arm_alone_mm": max(reachable), "best_with_the_bounded_advance_mm": max(advanced),
                                     "required_centre_margin_mm": PLACE_REQUIRED_CENTRE_MM, "bounded_base_advance_limit_mm": BASE_MAX_TOTAL_ADVANCE_MM,
                                     **placement_refusal_values(anchor, direction, PLACE_SIDE_DRIFT_ALLOWANCE_MM)})

    @_hook
    def base_pulse_refusal(self, code: str, measured: dict, holding: bool, home) -> None:
        """Which half of the base-pulse gate refused: the measured gripper against its declared phase, or the measured arm
        against the saved low scan pose. Both are scalars the controller had already read, and the saved pose is the dict
        the refusal itself was computed from: nothing is read a second time here."""
        if code.endswith("posture"):
            off = max(abs(float(measured.get(name, float("nan"))) - float(home[name])) for name in SCAN_JOINTS)
            return self.predicate("base_posture_at_the_low_scan_pose", False, measured=off, threshold=BASE_POSTURE_TOLERANCE_DEG, unit="deg", scalars=("arm_joint_offset_deg",),
                                  empirical=("BASE_POSTURE_TOLERANCE_DEG",), stamp_of=measured)
        self.predicate("gripper_in_its_declared_phase", False, measured=measured["gripper"], threshold=GRIPPER_HOLDING_BAND[0] if holding else GRIPPER_OPEN, unit="gripper_units",
                       scalars=("gripper_readback",), stamp_of=measured,
                       measurements={"gripper": measured["gripper"], "holding": bool(holding), "holding_band_min": GRIPPER_HOLDING_BAND[0], "holding_band_max": GRIPPER_HOLDING_BAND[1],
                                     "gripper_open": GRIPPER_OPEN, "gripper_open_tolerance": GRIPPER_OPEN_TOLERANCE})

    @_hook
    def start_pose_refused(self, measured: dict) -> None:
        """The cycle starts only from the low scan pose with the gripper open. Two scalars, both already read."""
        self.predicate("start_pose_is_the_low_scan_pose", False, measured=tool_state(measured)["lowest_link_m"], threshold=0.10, unit="m", scalars=("lowest_link_m", "gripper_readback"), stamp_of=measured,
                       measurements={"lowest_link_m": tool_state(measured)["lowest_link_m"], "gripper": measured["gripper"], "gripper_open": GRIPPER_OPEN, "gripper_open_tolerance": GRIPPER_OPEN_TOLERANCE})

    @_hook
    def base_measured(self, plausible: bool, moved: dict, frame_before, frame_after, holding: bool) -> None:
        if plausible:
            self.base_aside_mm = (self.base_aside_mm or 0.0) + float(moved["lateral_mm"])
        ids = self._ids(frame_before, frame_after)
        self._pad_measurements[bool(holding)] += [item for item in ids if item not in self._pad_measurements[bool(holding)]]
        self.predicate("base_measured", plausible, measured=moved["distortion_mm"], threshold=BASE_MAX_DISTORTION_MM, unit="mm", pad_measurements=(frame_before, frame_after),
                       scalars=("pad_cross_distortion_mm", "advance_mm", "lateral_mm", "pulse_duration_s"))

    def pad_measurements(self, holding: bool) -> list:
        return list(self._pad_measurements[bool(holding)])

    @_hook
    def automatic_retry(self, what: str) -> None:
        self.retries[what] = self.retries.get(what, 0) + 1

    @_hook
    def enter_policy(self, identity) -> None:
        """A stage procedure starts (stage_policy.StagePolicy.decides): its identity decides the predicates it declares. None
        (an identity that could not be computed) still takes its place, so the exit that follows pops it and no other."""
        self._policies.append(dict(identity) if identity else {"predicates": frozenset()})

    @_hook
    def exit_policy(self) -> None:
        if self._policies:
            self._policies.pop()

    # ------------------------------------------------------------------ actions
    def _next_action_id(self) -> str:
        self._actions += 1
        return f"act-{self._actions:04d}"

    def run_action(self, action_type, method, owner, args, kwargs):
        action = self._begin_action(action_type)
        try:
            result = method(owner, *args, **kwargs)
        except BaseException as error:
            self._end_action(action, None, error)
            raise  # the SAME exception, untouched: what the controller does with it is none of this object's business
        self._end_action(action, result, None)
        return result

    @_hook
    def _begin_action(self, action_type: str):
        self._close_base_group()
        self._action = {"id": self._next_action_id(), "type": action_type, "t0": time.monotonic_ns(), "planned": False, "started": False, "abort": None}
        return self._action

    @_hook
    def _end_action(self, action, result, error) -> None:
        self._close_base_group()
        if action is None:
            return
        try:
            duration = max(0, time.monotonic_ns() - action["t0"])
            abort = str(action["abort"] or "")
            code = "operator_abort" if self._stop_path else next((code for start, code in _ABORT_CODES if abort.startswith(start)), "aborted" if abort else "failed")
            if error is None:
                if action["started"]:  # a plan-only action ends at action.planned: nothing started, so nothing completed
                    self._emit("action.completed", {"action_type": action["type"], "result": "ok", "duration_ns": duration})
            elif not isinstance(error, Exception):
                pass  # KeyboardInterrupt, SystemExit: the process is leaving; what the action became is not known here
            elif action["started"]:
                self._note_ending(code, abort or error)  # the executor's own ending: a fallback reason, used only where no raise site of the controller stated one
                self._emit("action.completed", {"action_type": action["type"], "result": "stopped" if self._stop_path else "failed", "duration_ns": duration}, reason_code=code)
                self._say(abort or error, reason_code=code, level="error")
            else:  # nothing of this action was commanded. A gate of the controller refused it, or the executor ended it before its first segment.
                if isinstance(error, (ScanRefused, CycleFailed)):
                    code = f"{action['type']}.{'refused_before_plan' if not action['planned'] else 'refused_after_plan'}"
                    error._kc_stated = True  # so that main()'s own 'REFUSED before any motion' does not state the same refusal a second time
                self._note_ending(code, abort or error)
                self._emit("action.refused", {"action_type": action["type"], "before_motion": not self._moved, "detail": self._text(abort or error)}, reason_code=code)
        finally:
            self._action = None

    def command_sent(self) -> None:
        action = self._action
        if action is not None and not action["started"]:
            self._action_started(action)

    @_hook
    def _action_started(self, action) -> None:
        action["started"] = self._moved = True
        self._emit("action.started", {"action_type": action["type"]})

    @_hook
    def arm_pose_planned(self, label, planning_ns, plan, poses, holding, floor_m, pan_allowance, plan_only) -> None:
        self.timing("planning", planning_ns, "plan_only_pass")
        target = poses[-1]
        parameters = [_named("label", label), _named("pose_id", target.pose_id), _named("poses", len(poses)), _named("plan_token", plan.plan_digest), _named("holding", bool(holding)),
                      _named("floor_m", floor_m, "m"), _named("low_pan_allowance_deg", pan_allowance, "deg")]
        parameters += [_named(f"start.{name}", plan.start_pose[name], "deg") for name in SCAN_JOINTS] + [_named(f"target.{name}", target.joints[name], "deg") for name in SCAN_JOINTS]
        for index, segment in enumerate(plan.segments):
            parameters += [_named(f"segment.{index}.kind", segment.kind), _named(f"segment.{index}.commanded_s", segment.duration_s * SLOW_FACTOR, "s")]
        self._planned(plan_only, parameters)

    @_hook
    def gripper_planned(self, label, target, measured, digest, plan_only) -> None:
        self._planned(plan_only, [_named("label", f"grip {label}"), _named("target", target, "gripper_units"), _named("measured", measured["gripper"], "gripper_units"), _named("plan_token", digest),
                                  _named("move_s", GRIPPER_MOVE_S * SLOW_FACTOR, "s"), _named("hold_s", GRIPPER_HOLD_S, "s")])

    @_hook
    def base_pulse_planned(self, vx_mps, duration_s, measured, holding, move_base) -> None:
        self._planned(False, [_named("vx_mps", vx_mps, "m/s"), _named("duration_s", duration_s, "s"), _named("holding", bool(holding))] + [_named(f"arm_held.{name}", measured[name], "deg") for name in SCAN_JOINTS])
        consumed = getattr(move_base, "consumed_reads", None)
        if consumed is not None:
            del consumed[:]  # from here on it holds the reads of THIS pulse only
        if self._action is not None:  # the primitive sends the commands itself: handing it the pulse is the last thing this controller sees of the start
            self._action_started(self._action)

    def _planned(self, plan_only, parameters) -> None:
        if self._action is not None:
            self._action["planned"] = True
            self._emit("action.planned", {"action_type": self._action["type"], "plan_only": bool(plan_only), "parameters": parameters})

    @_hook
    def action_measured(self, measured: dict, abort_reason, gripper_read: bool = False) -> None:
        if self._action is not None:
            self._action["abort"] = abort_reason
        self.measured_state(measured)
        if gripper_read:
            self._gripper_read = (float(measured["gripper"]), self._stamp_behind(measured))

    @_hook
    def base_stop_confirmed(self, stopped: bool, move_base) -> None:
        """The primitive's OWN verdict on its stop, with the reads it took for it when its clients stamp them (the clients are
        the primitive's, built per pulse; they hand their reads over through the factory main() gave make_move_base)."""
        # The primitive's OWN read dicts, not copies: _sample_id() remembers on them the name it minted for an unstamped read.
        reads = list(getattr(move_base, "consumed_reads", None) or [])
        ids, fresh = self._state_samples(reads) if reads else ([], [])
        evidence = [{"type": "scalar", "observation_ids": ids, "scalar_name": "base_velocity_reads"}, {"type": "scalar", "observation_ids": fresh, "scalar_name": "base_velocity_fresh_reads"}] if reads else \
                   [{"type": "scalar", "observation_ids": [], "scalar_name": "primitive_return_value"}]
        self._emit("predicate.evaluated", {"predicate": "base_stop_confirmed_by_primitive", "value": bool(stopped), "threshold": None, "measured": None, "unit": None, "evidence": evidence,
                                           "consumed_source_stamp": reads[-1]["stamp"] if reads else None}, provenance=self._provenance(ids))
        self.capability_pulses(move_base)

    @_hook
    def capability_pulses(self, move_base) -> None:
        """One capability_output_applied per base pulse the Cake-managed capability relayed (the relay's own pulse record,
        autonomy/motion/capability_pulse.py): the slot generation and the producer pid its snapshot read, the commands it
        accepted, the ones the gateway APPLIED and the ones it dropped. value: at least one command was applied. The mission
        result aggregates the same records (Cycle._capability_evidence); this states each pulse where it happened. Nothing
        for a primitive that is not the relay's."""
        pulses = getattr(getattr(move_base, "capability", None), "pulses", None)
        if not isinstance(pulses, list):
            return
        for pulse in pulses[self._capability_pulses_stated:]:
            if not isinstance(pulse, dict):
                continue
            applied = int(pulse.get("commands_applied") or 0)
            dropped = sum(int(count) for count in (pulse.get("commands_dropped") or {}).values())
            self.predicate("capability_output_applied", applied > 0, measured=applied, threshold=1, unit=None, scalars=("commands_applied", "commands_accepted", "slot_generation", "producer_pid"),
                           measurements={"slot_generation": pulse.get("slot_generation"), "producer_pid": pulse.get("producer_pid"), "commands_accepted": int(pulse.get("commands_accepted") or 0),
                                         "commands_applied": applied, "commands_dropped": dropped})
        self._capability_pulses_stated = len(pulses)

    # ------------------------------------------------------------------ captures, perception, timing
    @_hook
    def timing(self, span: str, started_ns: int, reason_code=None, observation_id=None, duration_ns=None, clock_domain: str = "laptop_monotonic") -> None:
        duration = max(0, time.monotonic_ns() - started_ns) if duration_ns is None else max(0, int(duration_ns))
        self._emit("timing.span", {"span": span, "duration_ns": duration, "clock_domain": clock_domain}, reason_code=reason_code, observation_id=observation_id)

    @_hook
    def camera_ready(self, camera) -> None:
        setup = getattr(camera, "setup_timing", None)
        if setup:  # the remote mkdir is time spent WAITING on the robot's computer; the copy of the probe files is a transfer
            self.timing("waiting", 0, "camera_setup_remote_command", duration_ns=setup["ssh_ns"])
            self.timing("transfer", 0, "camera_setup_copy", duration_ns=setup["copy_ns"])

    @_hook
    def capture_timing(self, camera, observation_id) -> None:
        taken = getattr(camera, "last_timing", None)
        if taken:
            self.timing("capture", 0, "capture_remote_lifecycle", observation_id=observation_id, duration_ns=taken["capture_ns"])
            self.timing("transfer", 0, "capture_copy_back", observation_id=observation_id, duration_ns=taken["transfer_ns"])
            camera.last_timing = None  # stated once: the next observation must not inherit it

    def scan_log(self, log, camera):
        def watched(event, **fields):
            log(event, **fields)
            if event == "SCAN_OBSERVATION":  # the executor names the observation its capture became right after it stored it
                self.capture_timing(camera, fields.get("observation_id"))
            self._scan_said()
        return watched

    @_hook
    def _scan_said(self) -> None:
        self._close_base_group()

    @_hook
    def detector_made(self, first: bool, started_ns: int, detector) -> None:
        if not first:
            return
        if type(detector).__module__ == "zero_shot_detector":
            self._model_identity = getattr(sys.modules.get("zero_shot_detector"), "MODEL_NAME", None)
        elif type(detector).__name__ == "RemoteCubeDetections":  # the desktop service: the model and revision it stated at the ACCEPTED ping
            self._model_identity = getattr(detector, "model_identity", None)
        # make_detector() builds the object; THAT detector loads its weights on its first detect(). Whether it has not loaded
        # yet is visible, and then the first inference span is marked as the one that contains the load.
        self._lazy_model = getattr(detector, "_model", True) is None
        self.timing("model_init", started_ns, "detector_constructed_weights_load_at_first_inference" if self._lazy_model else "detector_constructed")

    @_hook
    def captured_without_frame(self, record: dict, before: set) -> None:
        observation_id = record.get("observation_id")
        if observation_id in before or not self._ids(observation_id):
            return
        camera = record.get("camera") or {}
        self._emit("observation.captured", {"source_role": camera.get("role") if camera.get("role") in ("wrist", "front") else "other", "media": "frame", "width_px": None, "height_px": None, "content_digest": None,
                                            "source_stamp": None, "retained": False}, observation_id=observation_id)
        self._gap_once(f"frame_missing.{observation_id}", "frame_missing", "a capture delivered no frame; its record is kept as evidence and nothing was perceived from it", [observation_id], reason_code="capture_without_frame")

    @_hook
    def perceived(self, record: dict, perception: dict, started_ns: int, frame_path, detector) -> None:
        duration = max(0, time.monotonic_ns() - started_ns)
        observation_id = record["observation_id"]
        if not self._ids(observation_id):
            return
        camera, lifecycle = record.get("camera") or {}, record.get("lifecycle") or {}
        digest = None
        try:
            digest = "sha256:" + hashlib.sha256(Path(frame_path).read_bytes()).hexdigest()  # of the STORED frame: the bytes the perception read
        except OSError:
            pass
        self._emit("observation.captured", {"source_role": camera.get("role") if camera.get("role") in ("wrist", "front") else "other", "media": "frame", "width_px": camera.get("width_px"), "height_px": camera.get("height_px"),
                                            "content_digest": digest, "source_stamp": None, "retained": True}, observation_id=observation_id)
        for span, key, code in (("capture", "total_latency_s", "camera_lifecycle_on_the_robot"), ("waiting", "open_latency_s", "camera_open_on_the_robot")):
            if _number(lifecycle.get(key)) is not None:  # measured by the probe on the robot's computer, in ITS monotonic clock
                self.timing(span, 0, code, observation_id=observation_id, duration_ns=int(float(lifecycle[key]) * 1e9), clock_domain="pi_monotonic")
        self.timing("inference", 0, "perceive_including_weights_load" if self._lazy_model else "perceive", observation_id=observation_id, duration_ns=duration)
        self._lazy_model = False
        for thing in (perception, perception.get("pad_by_template"), perception.get("cube_by_template")):
            if isinstance(thing, dict):
                self._known[id(thing)] = (thing, observation_id)
        self._perception_layers(observation_id, perception)

    def _perception_layers(self, observation_id: str, perception: dict) -> None:
        """One perception.result PER LAYER, because the layers are independent facts: a pad print that did not resolve
        PAD_FRAME says nothing against the cube box of the same frame, and each layer's validity is its own result."""
        cube, pad, template, placed = perception.get("cube") or {}, perception.get("pad"), perception.get("pad_by_template"), perception.get("cube_by_template")
        box, contact = cube.get("bbox_xyxy") or [None] * 4, cube.get("contact_px") or [None] * 2
        layers = [("cube-recognizer", "detected" if cube.get("detected") else "not_detected", cube.get("confidence"), True,
                   [_named(f"bbox_{side}", value, "px") for side, value in zip(("x0", "y0", "x1", "y1"), box)] + [_named("contact_x", contact[0], "px"), _named("contact_y", contact[1], "px"),
                    _named("contact_available", bool(cube.get("contact_px")))] + [_named(f"clipped_{side}", bool(flag)) for side, flag in sorted((cube.get("clipped") or {}).items())]),
                  ("pad-outline-fit", "detected" if (pad or {}).get("detected") else "not_detected", (pad or {}).get("confidence"), True,
                   [_named("boundary_rms", (pad or {}).get("boundary_rms_px"), "px"), _named("visible_fraction", (pad or {}).get("visible_fraction")), _named("view_elevation", (pad or {}).get("view_elevation_deg"), "deg")])]
        if template is None:
            layers.append(("pad-print-landmarks", "error", None, False, [_named("template_available", False)]))
        else:
            layers.append(("pad-print-landmarks", "detected" if template.get("pad_frame_valid") else "not_detected", None, False,
                           [_named("template_available", True), _named("pad_identity", bool(template.get("pad_identity"))), _named("pad_frame_valid", bool(template.get("pad_frame_valid"))),
                            _named("landmark_inliers", template.get("landmark_inliers")), _named("reprojection_rms", template.get("reprojection_rms_px"), "px")]))
        centre = (placed or {}).get("cube_center_pad_m") or [None] * 2
        # This layer only exists where PAD_FRAME does: static_world_observation writes cube_by_template only when the pad
        # print fixed the plane AND the cube was recognized with a contact point. Without the plane the layer never ran, so
        # it says error, as the landmark layer above does for the same cause; not_detected is kept for what it really means,
        # a valid PAD_FRAME in which this frame's cube could not be placed.
        layers.append(("cube-in-pad-frame", "detected" if placed else ("not_detected" if (template or {}).get("pad_frame_valid") else "error"), None, True,
                       [_named("cube_center_pad_x", centre[0], "m"), _named("cube_center_pad_y", centre[1], "m"), _named("inside_pad_outline", _flag((placed or {}).get("inside_pad_outline")))]))
        for detector, result, confidence, uses_model, measurements in layers:
            confidence = _number(confidence)
            self._emit("perception.result", {"detector": detector, "observation_ids": [observation_id], "result": result, "confidence": None if confidence is None else min(max(confidence, 0.0), 1.0),
                                             "measurements": measurements}, observation_id=observation_id, provenance=self._provenance([observation_id], model=uses_model, calibration=detector != "cube-recognizer"))

    # ------------------------------------------------------------------ stop
    @_hook
    def stop_observed(self, abort: dict, path: str) -> None:
        """The FIRST point of normal control flow that saw the flag. The handler only stored its own monotonic time in the
        abort dict; stop.requested has no field for it, so it is stated as the acknowledgement's latency, which is measured
        from exactly that instant, in the same clock."""
        if self._stop_path is not None:
            return
        self._stop_path, self._stop_requested = path, True
        requested_ns = abort.get("t_mono_ns")
        self._emit("stop.requested", {"origin": "operator_signal", "command_id": None}, action_id=None)
        self._emit("stop.acknowledged", {"acknowledged_by": "cycle-controller", "command_id": None, "latency_ns": None if requested_ns is None else max(0, time.monotonic_ns() - int(requested_ns))},
                   reason_code=f"stop_observed_on_{_id_text(path)}")

    def note_stop_flag(self, abort: dict) -> None:
        self._abort = abort

    @_hook
    def _stop_at_exit(self) -> None:
        """What THIS process knows about the stop when it ends. Whether the stop was honoured in time is the supervisor's
        verdict; motion_ceased is true only where something measured it."""
        abort = self._abort or {}
        if not abort.get("set"):
            return
        if self._stop_path is None:  # the flag was set and no control point read it before the run ended by itself
            self._stop_requested = True
            self._emit("stop.requested", {"origin": "operator_signal", "command_id": None})
            return self._emit("stop.outcome", {"outcome": "ceased_unverified", "motion_ceased": None, "evidence": "the stop request was recorded by the signal handler and no control point observed it before the run ended by itself; "
                                               "the process is ending and commands nothing further; nothing measured whether motion ceased"}, reason_code="stop_not_observed_before_the_run_ended")
        if self._stop_path == "base_after_pulse":
            return self._emit("stop.outcome", {"outcome": "holding", "motion_ceased": True, "evidence": "the base pulse in progress ran to its bounded end; the primitive confirmed its stop, this executor read the base at zero, "
                                               "the arm joints had not moved and the displacement was measured against the pad; nothing further was commanded"}, reason_code="stop_honoured_base")
        self._emit("stop.outcome", {"outcome": "ceased_unverified", "motion_ceased": None, "evidence": "no further command was sent once the request was observed; the arm holds its last commanded position under torque; "
                                    "nothing measured whether motion ceased"}, reason_code="stop_honoured_base" if self._stop_path == "base" else "stop_honoured_arm")


def start_events(args):
    """The events object of this run. Without KC_EVENTS_PATH it is the null object and NOTHING else happens: no import, no
    thread, no file. With it, the emitter of packages/demo-runtime is made; the run id falls back to the scan id, because the
    contract wants one on run.started. Whatever fails here ends in the null object and one line on stderr (never stdout,
    never a value): a run is not refused, delayed or changed because its record could not be started."""
    if not os.environ.get("KC_EVENTS_PATH"):
        return _NO_EVENTS
    try:
        run_id = os.environ.get("KC_RUN_ID") or args.scan_id
        if not run_id:
            # run.started, run.config and run.finished REQUIRE a run id (event.schema.json pins run_id to common#/$defs/id
            # for those three kinds). An emitter built with none would write exactly those three records, all invalid, and
            # nothing later could repair them; starting no emitter at all is the honest end, as for any other failure here.
            raise ValueError("neither KC_RUN_ID nor the scan id names this run, and the contract requires a run id")
        emitter = cli.import_runtime("demo_runtime.emit").make_emitter({**os.environ, "KC_RUN_ID": run_id}, producer_name="single-cube-cycle")
        tokens = [args.remote_ip, args.robot_id, args.pi_ssh or "", *(args.pi_ssh or "").split("@"), str(ROOT), os.path.expanduser("~")]
        return CycleEvents(emitter, tokens)
    except Exception as error:  # noqa: BLE001
        print(f"events are OFF for this run: the emitter could not be started ({type(error).__name__})", file=sys.stderr)
        return _NO_EVENTS


LEASE_GENERATION_ENV, LEASE_SESSION_ENV = "KC_HOST_LEASE", "KC_HOST_SESSION"  # documented at the top of this module


def host_lease_metadata(environ=None):
    """The two ownership keys bin/pi/host_boundary.py admits, when the environment names a lease; None when it names none,
    which is the case that changes nothing. ScanRefused when it names HALF a lease or a pair the host could never have
    issued: the refusal names the two variables and never repeats a value, and it happens before the robot is constructed.
    The keys themselves are built by demo_runtime.lease_client, which owns them; this function only reads and refuses."""
    environ = os.environ if environ is None else environ
    generation, session = environ.get(LEASE_GENERATION_ENV) or "", environ.get(LEASE_SESSION_ENV) or ""
    if not generation and not session:
        return None
    if not generation or not session:
        raise ScanRefused(f"a host command lease needs both {LEASE_GENERATION_ENV} and {LEASE_SESSION_ENV}; this environment sets only one of them, "
                          f"and a host with its boundary on would drop every command of this run")
    try:
        return cli.import_runtime("demo_runtime.lease_client").command_metadata(generation, session)
    except ValueError as error:  # the sentence of the lease client states the SHAPE that was expected, never the value that was given
        raise ScanRefused(f"{LEASE_GENERATION_ENV} and {LEASE_SESSION_ENV} do not name a lease this host could have issued: {error}") from None
    except ImportError:
        raise ScanRefused(f"{LEASE_GENERATION_ENV} and {LEASE_SESSION_ENV} name a host command lease and the lease client of this checkout cannot be "
                          f"imported: running without the metadata would be a silent downgrade to commands the host drops") from None


def attach_lease(robot_client, metadata) -> None:
    """The ARM path: demo_runtime.lease_client wraps this client's send_action, so every action run_pose and grip send
    leaves with the two keys on a copy of it. The BASE path takes the same metadata through make_move_base."""
    cli.import_runtime("demo_runtime.lease_client").attach_lease_metadata(robot_client, metadata)


def make_move_base(remote_ip: str, robot_id: str, client_factory=None, lease_metadata=None, forward=None):
    """move_base(vx_mps, duration_s) -> stop confirmed. FORWARD is the physically proven primitive itself, imported unchanged
    (MobileApproachController -> StableMotionGateway -> LeKiwiTransport, refreshed 150 ms lease, explicit zero, repeated
    zero reads; physical pass 2026-09-18). REVERSE follows the pattern validated on hardware by the reverse restore of that
    work: the controller cannot express a reverse target, so DesiredBaseMotion goes straight into the SAME unmodified
    gateway (its clamps, slew limits and lease), every refresh with a fresh command id.

    client_factory (None: the stock client, as before) only decides which CLASS answers the transport's get_observation():
    the one that also stamps each read, so that the reads behind a stop confirmation can be stated like any other read.
    lease_metadata (None: nothing is attached, as before) is the host command lease every action of a pulse then carries.
    Both travel the SAME seam of lerobot_transport, because the forward primitive builds its own transport per pulse.

    forward (None: the proven primitive above, as before) REPLACES the forward half, for a mission target that requires the
    capability: the Cake-managed capability's relay (make_capability_relay), whose every velocity is the capability's own
    output. With it given, run_hybrid_pick_place is not even imported, so the in-process MobileApproachController cannot
    perform the approach. The reverse half is the proven one either way (decision D6). The returned move_base then also
    carries `capability` (the relay, for its evidence) and `last_refusal` (the relay's, for the pulse just run; None after
    a reverse pulse), which base_pulse refuses on after its own base-zero and arm-drift checks."""
    sys.path.insert(0, str(ROOT / "autonomy" / "motion"))
    relay = forward
    if relay is None:
        from run_hybrid_pick_place import _make_move_base_sustained

    transport_options = {**({} if client_factory is None else {"client_factory": client_factory}),
                         **({} if lease_metadata is None else {"lease_metadata": lease_metadata})}
    forward = _make_move_base_sustained(remote_ip, robot_id) if relay is None else relay
    if transport_options:  # that primitive builds its own transport per pulse and takes neither: it is given both for the length of its call
        from lerobot_transport import client_factory_scope

        proven_forward = forward

        def forward(vx_mps: float, duration_s: float) -> bool:
            with client_factory_scope(client_factory, lease_metadata):
                return proven_forward(vx_mps, duration_s)

    def reverse(vx_mps: float, duration_s: float) -> bool:
        import uuid

        from clock import MonotonicClock
        from lerobot_transport import LeKiwiTransport
        from mobile_approach import DesiredBaseMotion
        from stable_motion_gateway import GatewayLimits, StableMotionGateway

        transport = LeKiwiTransport(remote_ip=remote_ip, robot_id=robot_id, **transport_options)
        transport.connect()
        clock = MonotonicClock()
        gateway = StableMotionGateway(transport, clock, GatewayLimits(vx_max_mps=abs(vx_mps), vy_max_mps=0.05, omega_max_rps=0.01))
        gateway.enable_autonomous()
        try:
            started = time.perf_counter()
            while time.perf_counter() - started < duration_s:
                now_ns = clock.now_ns()
                gateway.submit_command(DesiredBaseMotion(command_id=f"cycle-rev-{uuid.uuid4().hex[:8]}", target_id="single_cube_cycle_return", timestamp_ns=now_ns,
                                                         vx_mps=vx_mps, vy_mps=0.0, omega_rps=0.0, expiry_ns=now_ns + 150 * 1_000_000))
                time.sleep(0.08)
            gateway.disable_autonomous()
            time.sleep(0.3)
            for _ in range(6):
                observation = transport._client.get_observation()
                if reads_at_rest(observation):  # formerly exact zero on all three axes (operator ruling 2026-09-27)
                    return True
                time.sleep(0.15)
            return False
        finally:
            gateway.operator_stop()
            transport.disconnect()

    if relay is None:
        move_base = lambda vx_mps, duration_s: forward(vx_mps, duration_s) if vx_mps > 0 else reverse(vx_mps, duration_s)  # noqa: E731 -- the proven one-liner, named only to carry the attribute below
        move_base.consumed_reads = getattr(client_factory, "consumed_reads", None)  # the reads the primitive's own clients served, when they are the stamping kind
        return move_base

    def relayed(vx_mps: float, duration_s: float) -> bool:
        """The same dispatch as the one-liner, and after a forward pulse the relay's refusal of THAT pulse, read back."""
        if vx_mps > 0:
            relayed.last_refusal = None
            try:
                return forward(vx_mps, duration_s)
            finally:
                relayed.last_refusal = getattr(relay, "last_refusal", None)
        relayed.last_refusal = None
        return reverse(vx_mps, duration_s)

    relayed.capability, relayed.last_refusal = relay, None
    relayed.consumed_reads = getattr(client_factory, "consumed_reads", None)
    return relayed


CAPABILITY_HOST = "127.0.0.1"  # the relay drives the motor host on the robot it runs on: the host's command port is bound to loopback (handoff 12.2)
CAPABILITY_ADMIN_TIMEOUT_S = 5.0  # one admin-probe read (list-slots or get-health), as the qualifying probe bounds it
CAPABILITY_LINK_TIMEOUT_S = 5.0  # the wait for the capability's own subscription before a pulse, as the qualifying probe bounds it
# capability_pulse.STOPPED_BEFORE_MOTION, the relay's refusal when this run's stop flag was set before it commanded anything.
# The literal, because this module imports the relay only when a mission target requires it; a test pins the two equal.
RELAY_STOPPED_BEFORE_MOTION = "operator_abort_before_motion"


def make_capability_relay(target, robot_id: str, transport_options: dict, should_stop, observe, motion_epoch):
    """The forward half of make_move_base for a mission run that CARRIES the capability: capability_pulse's relay, bound
    to the slot, capsule and endpoints the target file names and to this target's id. Building it reads and opens
    nothing; every pulse takes its own snapshot of the capability and refuses without an ACTIVE slot and a launched pid.
    Its transports are built per pulse, to the motor host on this robot, with this run's client factory and host lease.
    should_stop() is this run's stop flag: the relay reads it again before its Cake reads, right after them and once the
    link is open, because those take seconds after base_pulse's own check (capability_pulse, THE STOP). observe() is
    Cycle.observed_target, the ACTUAL v2 observation of this target (the remaining bounded advance of the view the loop
    pulses on, from a stamped stationary capture, with a perception budget) or a refusal naming every reason there is
    none (the relay then reads nothing and builds nothing), and motion_epoch() is the Cycle's count of ended motions."""
    sys.path.insert(0, str(ROOT / "autonomy" / "motion"))
    from capability_link import admin_reader
    from capability_pulse import CapabilityPulse
    from lerobot_transport import LeKiwiTransport

    endpoints = target.capability
    return CapabilityPulse(read=admin_reader(endpoints.admin_probe, endpoints.admin_socket, CAPABILITY_ADMIN_TIMEOUT_S), slot_uuid=endpoints.slot_uuid,
                           capsule=endpoints.capsule, observations=endpoints.observation_endpoint, commands=endpoints.command_endpoint,
                           transport_factory=lambda: LeKiwiTransport(remote_ip=CAPABILITY_HOST, robot_id=robot_id, **transport_options),
                           target_id=target.target_id, should_stop=should_stop, observe=observe, motion_epoch=motion_epoch,
                           link_timeout_s=CAPABILITY_LINK_TIMEOUT_S,
                           consumed_reads=getattr(transport_options.get("client_factory"), "consumed_reads", None))


def mission_refusal(target, args):
    """(reason code, sentence) when this invocation cannot carry the mission target it was given, else None. Checked before
    the robot is even constructed. The sentences name no value: the target file holds paths."""
    if args.do != "cycle":
        return "mission_target_needs_the_cycle", "a mission target is carried by the full cycle and by nothing else (--do cycle)"
    if args.plan_only:
        return "mission_target_not_plan_only", "a mission target is executed, never only planned"
    if target.run_id != args.scan_id:  # the run id IS the observation session: evidence stored under another id would belong to no run the runtime knows
        return "mission_target_names_another_run", "the mission target names another run than this one (--scan-id)"
    if target.carries_capability and args.remote_ip != CAPABILITY_HOST:
        return "mission_capability_host_not_local", "a run that carries the capability runs on the robot beside it, against the motor host on loopback (--remote-ip)"
    if target.radii is None:  # target_binding, THE TWO RADII: no qualified claim and match radii, no mission run
        return "mission_radii_unqualified", "the mission target carries no claim and match radii, and a mission target is never selected or attributed with unqualified ones"
    return None


RUNTIME_MODE_ENV, RUNTIME_MODE_LIVE = "KC_RUNTIME_MODE", "live"  # demo_runtime.supervisor.RUNTIME_MODE_ENV and LIVE; a test keeps them equal
LIVE_FORWARD_EXCLUDED = "synthetic_observation_excluded_from_live"


def _forward_action(do: str) -> bool:
    """base:SIGNED_S with a positive (forward) duration. Anything that does not parse is left to the action itself."""
    try:
        return float(do.partition(":")[2]) > 0
    except ValueError:
        return False


def runtime_is_live(environ=None) -> bool:
    """The runtime that started this run says it is LIVE (the module docstring, THE SYNTHETIC OBSERVATION IS OUT OF A LIVE
    RUN). Absent, or any other word, is not LIVE: a run started by hand has no runtime mode."""
    environ = os.environ if environ is None else environ
    return environ.get(RUNTIME_MODE_ENV) == RUNTIME_MODE_LIVE


def live_forward_refused(vx_mps: float, duration_s: float) -> bool:
    """The forward half of make_move_base in a LIVE run without a mission target: it commands nothing and confirms no
    stop. base_pulse refuses a forward pulse of such a run before it gets here; this is the structural layer under that
    refusal, so that the primitive fed the synthetic observation is not even imported."""
    live_forward_refused.last_refusal = LIVE_FORWARD_EXCLUDED
    return False


live_forward_refused.last_refusal = LIVE_FORWARD_EXCLUDED


# The four variables of DESKTOP PERCEPTION (the module docstring), spelled as demo_runtime.supervisor exports them (a test
# keeps the two equal), and the robot resident's session the run was admitted under.
PERCEPTION_PORT_ENV, PERCEPTION_DEADLINE_ENV = "KC_PERCEPTION_REMOTE_PORT", "KC_PERCEPTION_DEADLINE_S"
PERCEPTION_MODEL_REVISION_ENV, PERCEPTION_MODEL_FILES_ENV = "KC_PERCEPTION_MODEL_REVISION", "KC_PERCEPTION_MODEL_FILES_DIGEST"
RESIDENT_SESSION_ENV = "KC_RESIDENT_SESSION"


class PerceptionRefusedBeforeConnect(Exception):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code, self.detail = reason_code, detail


def make_remote_perception(args, mission, environ=None):
    """The desktop perception client of this run, pinged and accepted, or None when the environment names no port. Raises
    PerceptionRefusedBeforeConnect (the caller refuses before the robot is constructed) for an incomplete environment, a
    tree that is not the measured perception, and a service that does not answer as this run's service. Reads nothing
    from the robot and commands nothing."""
    environ = os.environ if environ is None else environ
    if not environ.get(PERCEPTION_PORT_ENV) or args.plan_only:  # a plan perceives nothing, so it asks nobody
        return None
    import remote_perception  # autonomy/perception, on the path since this module's imports
    import static_world_observation as swo

    try:
        port, deadline_s = int(environ[PERCEPTION_PORT_ENV]), float(environ.get(PERCEPTION_DEADLINE_ENV) or "nan")
        revision, files = environ.get(PERCEPTION_MODEL_REVISION_ENV) or "", environ.get(PERCEPTION_MODEL_FILES_ENV) or ""
        if not (0 < port < 65536 and math.isfinite(deadline_s) and deadline_s > 0 and revision and files):
            raise ValueError("incomplete")
    except ValueError:
        raise PerceptionRefusedBeforeConnect("perception_remote_config_incomplete", "the desktop perception is named without all four of its values "
                                             "(port, deadline, model revision, model files digest)") from None
    template = swo.TEMPLATE_DIR if cli._PAD_TEMPLATE_DIR is None else cli._PAD_TEMPLATE_DIR  # the print perceive would read here
    try:
        expect = remote_perception.robot_identity(ROOT, template, model_revision=revision, model_files_digest_value=files)
        robot = {"resident_session": environ.get(RESIDENT_SESSION_ENV) or None, "run_id": environ.get("KC_RUN_ID") or args.scan_id,
                 "mission_id": None if mission is None else mission.mission_id, "target_id": None if mission is None else mission.target_id,
                 "scan_id": args.scan_id, "controller_pid": os.getpid()}
        client = remote_perception.RemotePerception(port=port, deadline_s=deadline_s, expect=expect, robot=robot)
        client.ping()
    except remote_perception.RemotePerceptionRefused as refusal:
        raise PerceptionRefusedBeforeConnect(f"perception_remote_{refusal.reason_code}", f"the desktop perception is not usable ({refusal.reason_code}"
                                             + (f", service: {refusal.service_reason}" if refusal.service_reason else "") + ")") from None
    return client


def make_robot(remote_ip: str, robot_id: str, stamped: bool = False, require_fresh: bool = False):
    """(the robot client, the client factory for the base primitive or None). stamped False is today's construction, to the
    letter. stamped True (a runtime configuration or events were asked for) builds the SAME stock client class behind
    demo_runtime.fresh_client: it returns what the stock client returns and additionally says, per read, whether that
    observation was new. require_fresh is the refusal of that module, OFF unless the configuration turns it on: with it
    off, no read behaves differently. The base primitive opens clients of its own for every pulse; the factory makes those
    from the same class and keeps what they served, so that a stop confirmation can name its reads."""
    if not stamped:
        from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

        return LeKiwiClient(LeKiwiClientConfig(remote_ip=remote_ip, id=robot_id)), None
    fresh = cli.import_runtime("demo_runtime.fresh_client")
    from lerobot.robots.lekiwi import LeKiwiClient, LeKiwiClientConfig

    stamping = fresh.make_fresh_client_class(LeKiwiClient)
    consumed = []

    class PrimitiveClient(stamping):
        def get_observation(self):
            observation = super().get_observation()
            try:
                consumed.append({"observation": observation, "stamp": self.to_source_stamp(), "detail": self.last_stamp(), "session": self.local_rx_session})
                del consumed[:-32]  # a confirmation takes six reads; nothing may grow without bound inside a primitive
            except Exception:  # noqa: BLE001 -- bookkeeping must never reach the primitive
                pass
            return observation

    def factory(address: str, identity: str):
        client = PrimitiveClient(LeKiwiClientConfig(remote_ip=address, id=identity))
        client.set_require_fresh(bool(require_fresh))
        return client

    factory.consumed_reads = consumed
    robot = stamping(LeKiwiClientConfig(remote_ip=remote_ip, id=robot_id))
    robot.set_require_fresh(bool(require_fresh))
    return robot, factory


def fmt_tool(state: dict) -> str:
    return f"{state['out_mm']:+.1f} mm out / {state['up_mm']:+.1f} mm up vs the proven grasp, pitch {state['pitch_deg']:.1f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run_single_cube_cycle.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--do", required=True, help="observe | pan:DEG | move:FORWARD_MM,DZ_MM | grip:latch | grip:open | base:SIGNED_S | exit | cycle | scan")
    parser.add_argument("--holding", action="store_true", help="the cube is held: the gripper key stays in the commands (held at its measured value) and the holding band is enforced")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--remote-ip", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--pi-ssh", default=os.environ.get("KC_PI_SSH"))
    parser.add_argument("--scan-id", default="manipulation-" + time.strftime("%Y%m%d", time.gmtime()))
    # WHICH PROVIDER OPENS THE CAMERA. Absent means the legacy per-capture path, so an argv that does not name one is
    # the argv every recorded run was produced with, unchanged. The runtime appends these two only when its
    # configuration says the camera service owns the camera.
    parser.add_argument("--capture-provider", choices=cli.CAPTURE_PROVIDERS, default=cli.CAPTURE_PROVIDER_LEGACY)
    parser.add_argument("--mission-port", type=int, default=5570)
    args = parser.parse_args(argv)
    # The events object exists from here to the OUTERMOST finally below, which is the only place that ends it. Nothing in it
    # can raise into this function, so the status _run() decided is the status that leaves, the disconnect inside _run()
    # has already happened by then, and the cycle record was written where it always was.
    events, exit_status, crashed = start_events(args), None, False
    try:
        exit_status = _run(parser, args, events)
        return exit_status
    except SystemExit as leaving:  # parser.error() and the live-pose refusal of the scan CLI leave this way
        exit_status = leaving.code if isinstance(leaving.code, int) else 0 if leaving.code is None else 1
        raise
    except BaseException:
        crashed = True  # a traceback or a Ctrl-C before the handler existed: the interpreter decides the status, not this function
        raise
    finally:
        set_placement_envelope((), None, None)  # the words are this run's: the module is back on the proven envelope whatever happened
        events.finish(exit_status, crashed)


def use_runtime_paths(paths) -> None:
    """The saved pose, the pad template and the observation root of an EXPLICIT runtime configuration, for this module and
    for the scan CLI it builds on (HOME_FILE exists in both, as one pinned line each)."""
    global HOME_FILE
    cli.use_runtime_paths(paths)
    HOME_FILE = cli.HOME_FILE


def _run(parser, args, events) -> int:
    events.run_started(args)
    # EXPLICIT CONFIGURATION. Without KC_RUNTIME_CONFIG nothing in this block does anything and startup is what it was: the
    # legacy paths, the stock client. With it, the three paths come from the configuration and the COMPLETE preflight runs
    # here, before the robot is even constructed: a missing saved pose is a refusal at start, never again a discovery in
    # the middle of a cycle with the cube held. The report's details name fields and fixed file names, never a value.
    try:
        paths = cli.runtime_paths()
    except Exception as error:  # noqa: BLE001 -- a config that was named and cannot be used is a refusal, never a silent legacy run
        print(f"REFUSED before any motion: {cli.config_refusal(error)}")
        events.refused_before_connect(args, "runtime_config_refused", cli.config_refusal(error))
        return 3
    stamped = paths.config is not None or bool(os.environ.get("KC_EVENTS_PATH"))
    require_fresh = bool(paths.config is not None and paths.config.freshness_refusal_enabled)
    events.run_config(args, paths.config, stamped, require_fresh)
    if paths.config is not None:
        use_runtime_paths(paths)
        report = cli.import_runtime("demo_runtime.preflight").run_preflight(paths.config, args.do)
        if not report.ok:
            refusal = "the preflight of the runtime configuration did not pass: " + "; ".join(f"{check.reason_code} ({check.detail})" for check in report.failed())
            print(f"REFUSED before any motion: {refusal}")
            events.refused_before_connect(args, report.reason_codes()[0], refusal)
            return 3
    # THE HOST COMMAND LEASE (the two variables at the top of this module). Resolved HERE, before the robot is constructed
    # and long before anything moves: with no lease named, `lease` is None and nothing below does anything at all; with a
    # lease named that the host could never have issued, this is a refusal at start rather than a cube in the jaws and a
    # host dropping one command after another. Neither value is printed, recorded or emitted.
    try:
        lease = host_lease_metadata()
    except ScanRefused as refusal:
        print(f"REFUSED before any motion: {refusal}")
        events.refused_before_connect(args, "host_lease_malformed", str(refusal))
        return 3
    # THE MISSION TARGET (the module docstring, MISSION MODE). With KC_MISSION_TARGET absent, `mission` is None and every
    # mission hook of the Cycle is a no-op. A file that is named and does not parse, or that this invocation cannot carry, is
    # a refusal HERE, before the robot is constructed. The refusal names a reason code and a field, never a value.
    try:
        mission = target_binding.mission_target_from_environ(os.environ)
    except target_binding.MissionFileRefused as refusal:
        print(f"REFUSED before any motion: the mission target file is refused ({refusal})")
        events.refused_before_connect(args, "mission_target_refused", f"the mission target file is refused ({refusal})")
        return 3
    unfit = None if mission is None else mission_refusal(mission, args)
    if unfit is not None:
        print(f"REFUSED before any motion: {unfit[1]}")
        events.refused_before_connect(args, unfit[0], unfit[1])
        return 3
    # THE SYNTHETIC OBSERVATION IS OUT OF A LIVE RUN (the module docstring). Not LIVE: `live` is False and nothing below
    # changes. LIVE without a mission target: a forward base action is refused HERE, before the robot is constructed.
    live = runtime_is_live()
    # THE PLACEMENT ENVELOPE (PLACEMENT_ENVELOPE_ENV above). Absent: the proven envelope and nothing below changes. A word no
    # stage declares, and a LIVE run naming a word no stage has qualified (envelope_words_qualified), are refused HERE, before the robot is
    # constructed, in the mission path and in the non-mission path alike; the attended label of the operator's config is the
    # one way past the second refusal, and it is stamped into the run's record.
    try:
        envelope = placement_envelope_words()
    except ScanRefused as refusal:
        print(f"REFUSED before any motion: {refusal}")
        events.refused_before_connect(args, "placement_envelope_unknown_word", str(refusal))
        return 3
    attended = (os.environ.get(PLACEMENT_ENVELOPE_ATTENDED_ENV) or "").strip() or None
    try:
        side_offset_force = placement_side_offset_force_deg()
    except ScanRefused as refusal:
        print(f"REFUSED before any motion: {refusal}")
        events.refused_before_connect(args, "placement_side_offset_force_refused", str(refusal))
        return 3
    if live and side_offset_force is not None and attended is None:
        forced = "a LIVE run names a forced release offset, which belongs to the attended qualification runs of side_offset, and the config names no attended label"
        print(f"REFUSED before any motion: {forced}")
        events.refused_before_connect(args, "placement_side_offset_force_not_attended", forced)
        return 3
    if live and envelope and not set(envelope) <= envelope_words_qualified() and attended is None:
        unqualified = "a LIVE run names an envelope word that is not qualified; the qualification record and the attended label are the two ways in, and the config names neither"
        print(f"REFUSED before any motion: {unqualified}")
        events.refused_before_connect(args, "placement_envelope_not_qualified", unqualified)
        return 3
    set_placement_envelope(envelope, attended, side_offset_force)
    if live and mission is None and args.do.partition(":")[0] == "base" and _forward_action(args.do):
        print("REFUSED before any motion: a forward base pulse of a LIVE run would be fed the synthetic observation, which is for rehearsal only")
        events.refused_before_connect(args, LIVE_FORWARD_EXCLUDED, "a forward base pulse of a LIVE run would be fed the synthetic observation, which is for rehearsal only")
        return 3
    # DESKTOP PERCEPTION (the module docstring). Absent: `perception` is None and perceive runs here, as it always did. Named:
    # the service is pinged HERE, before the robot is constructed, and a service that is not this run's is a refusal.
    try:
        perception = make_remote_perception(args, mission)
    except PerceptionRefusedBeforeConnect as refusal:
        print(f"REFUSED before any motion: {refusal.detail}")
        events.refused_before_connect(args, refusal.reason_code, refusal.detail)
        return 3
    try:
        robot, base_clients = make_robot(args.remote_ip, args.robot_id, stamped, require_fresh)
    except ImportError as error:
        if require_fresh:  # the operator asked for the refusal of stale reads: running without it would be a silent downgrade
            print(f"REFUSED before any motion: the configuration enables the freshness refusal and the freshness client cannot be imported ({type(error).__name__})")
            events.refused_before_connect(args, "freshness_client_unavailable", "the configuration enables the freshness refusal and the freshness client cannot be imported")
            return 3
        if not stamped:  # the stock construction itself failed (LeKiwiClient.__init__ imports zmq): the fallback below IS that
            raise  # same call, so it would only build the same client a second time and raise the same error, chained
        robot, base_clients = make_robot(args.remote_ip, args.robot_id)
    base_primitive = {} if base_clients is None else {"client_factory": base_clients}  # empty in the legacy path: make_move_base is called exactly as before
    if lease is not None:  # the ARM through this client (run_pose, grip); the BASE through every transport a pulse builds
        attach_lease(robot, lease)
        base_primitive["lease_metadata"] = lease
        print("HOST LEASE: the environment names one, so every command of this run carries the ownership metadata the motor host boundary admits")
    robot.connect()
    cli.wait_for_live_pose(robot)
    if mission is None and not live:  # the legacy wiring, to the letter
        move_base = make_move_base(args.remote_ip, args.robot_id, **base_primitive) if args.do.partition(":")[0] in ("cycle", "base") else None
    elif mission is None:  # LIVE: the same wiring with the forward half a refusal, so the synthetically fed primitive is never imported
        move_base = make_move_base(args.remote_ip, args.robot_id, forward=live_forward_refused, **base_primitive) if args.do.partition(":")[0] in ("cycle", "base") else None
    elif mission.carries_capability:  # forward only through the Cake-managed capability; the reverse is the proven primitive (D6)
        # The relay reads THIS run's stop flag, the Cycle's, built just below, and publishes what the Cycle resolves for this
        # target. The lambdas look `cycle` up when a pulse calls them, and no pulse can run before the Cycle exists; were one
        # to, the name would not resolve: the relay takes a flag it cannot read as a stop, and an observation or an epoch it
        # cannot read as none.
        relay = make_capability_relay(mission, args.robot_id, base_primitive, should_stop=lambda: cycle.abort["set"],
                                      observe=lambda: cycle.observed_target(), motion_epoch=lambda: cycle.motion_epoch)
        move_base = make_move_base(args.remote_ip, args.robot_id, forward=relay, **base_primitive)
    else:  # no capability carried: no base primitive at all, so a base pulse is refused wherever it would be reached
        move_base = None
    cycle = Cycle(robot, args.pi_ssh, args.scan_id, args.plan_only, move_base=move_base, events=events,
                  capture_provider=args.capture_provider, mission_port=args.mission_port, target=mission, perception=perception)
    cycle.runtime_live = live
    events.note_stop_flag(cycle.abort)
    # The handler stays trivial: two dict writes. It may store WHEN it ran (the first time only); it never emits, because the
    # emitter's lock is not re-entrant and a handler that interrupts emit() and then emits would deadlock with the arm moving.
    signal.signal(signal.SIGINT, lambda *_: (cycle.abort.setdefault("t_mono_ns", time.monotonic_ns()), cycle.abort.update(set=True)))
    try:
        measured = cycle.pose()
        events.measured_state(measured)
        print(f"LIVE pose { {k: round(v, 3) for k, v in measured.items()} }; tool {fmt_tool(tool_state(measured))}; base {cli.read_base(robot)}")
        kind, _, value = args.do.partition(":")
        floor = CONTACT_FLOOR_M
        if kind == "observe":
            cycle.observe(args.holding)
        elif kind == "pan":
            cycle.run_pose(pan_step_pose(measured, float(value), args.holding), args.holding, floor, pan_allowance=STEP_MAX_PAN_DEG)
        elif kind == "move":
            forward_mm, dz_mm = (float(v) for v in value.split(","))
            pose, report = cartesian_step_pose(measured, forward_mm / 1000.0, dz_mm / 1000.0, args.holding)
            print(f"PATH CHECK: deviation from the straight line {report['max_deviation_m'] * 1000:.1f} mm, lowest link on the path {report['lowest_link_m']:+.3f} m, elbow {report['elbow_range_deg'][0]:.1f}..{report['elbow_range_deg'][1]:.1f} deg, joint-limit margin {report['min_joint_limit_margin_deg']:.0f} deg")
            cycle.run_pose(pose, args.holding, floor)
        elif kind == "grasp":
            forward_mm, dz_mm = (float(v) for v in value.split(","))
            pose, report = proven_grasp_pose(measured, forward_mm / 1000.0, dz_mm / 1000.0)
            print(f"PATH CHECK: lowest link on the path {report['lowest_link_m']:+.3f} m (proven grasp tool height {GRASP_TOOL_Z_M:+.3f}), elbow {report['elbow_range_deg'][0]:.1f}..{report['elbow_range_deg'][1]:.1f} deg, joint-limit margin {report['min_joint_limit_margin_deg']:.0f} deg")
            cycle.run_pose(pose, False, floor)
        elif kind == "grip":
            cycle.grip({"latch": GRIPPER_LATCH, "open": GRIPPER_OPEN}[value], value)
        elif kind == "base":
            cycle.base_probe(float(value), args.holding)
        elif kind == "cycle":
            try:
                summary = cycle.run_cycle()
            except ScanRefused as refusal:  # inside a cycle a refused step comes AFTER other motion
                raise cycle.failed("step_refused_mid_cycle", f"a step was refused in the middle of the cycle: {refusal}", base_ahead_mm=cycle.base_ahead_mm)
            finally:
                print(f"BASE (measured against the pad): {cycle.base_ahead_mm:+.1f} mm from where this run started", flush=True)
                record = cycle.session_dir / f"cycle-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
                record.write_text(json.dumps(cycle.transitions, indent=1, default=float))
                print(f"state transitions: {record}")
            print(f"CYCLE DONE: {summary}")
            events.cycle_returned(summary)
        elif kind == "exit":
            cycle.return_to_low_pose()
        elif kind == "scan":  # the SEARCH stage's scene scan (policy_search.scene_scan); a ScanRefused before any motion is exit 3
            policy_search.scene_scan(cycle, _CONTROLLER_VIEW, bool(args.plan_only))
        else:
            parser.error(f"unknown action {args.do!r}")
        return cycle.mission_finished(0)  # mission_finished: the mission result file in mission mode, the status unchanged in every mode
    except ScanRefused as refusal:
        print(f"REFUSED before any motion: {refusal}")
        events.refused(args, refusal)
        return cycle.mission_finished(3)
    except CycleFailed as failure:
        print(f"CYCLE FAILED: {failure}. The arm HOLDS; base measured {cycle.base_ahead_mm:+.1f} mm from where this run started.")
        return cycle.mission_finished(5)
    except RuntimeError as error:
        print(f"STOPPED: {error}. The arm HOLDS.")
        return cycle.mission_finished(4, error)
    finally:
        robot.disconnect()


if __name__ == "__main__":
    sys.exit(main())
