#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_reach_policies_execution.py: the additive placement policies EXECUTED, end to end, on the scripted World
# of test_cycle_mission_mode.py (main() runs for real: run_pose, grip, observe, base_pulse, the planner, the executor, the
# store; only the robot client, the camera, the perception, the cube listing and the base primitive are scripted).
#
# The World here is made a little more physical (PhysicalWorld): the block is released where the arm and the base put it
# (the carried block's start moved by the base's travel and by the tool's travel from the close that held), and with
# loaded_reach_out_mm set, the LOADED arm sags past that out the way it did on record (a positive shoulder_lift error, the
# tool nearer and lower, never more than loaded_sag_max_deg: 4.5 deg by default, so the executor's own 5 deg arrival check
# never aborts a step; 2.0 deg, inside the recorded loaded errors, where the way back must come back for the fallback).
#
# THE LOADED REACH CAP (the operator's rulings of 2026-10-01, "cap at proven reach", and of 2026-10-02, Q8; placement_policy
# THE LOADED REACH CAP): a block the proven policy places, and any other block whenever a plan of the run under the cap fits
# the 120 mm budget (the operator's block of 2026-09-30 included), is carried by an additive policy only inside the proven
# loaded reach (95 mm out), no loaded command passing it, its plan counting only on the out such a command measures under
# the record (71 mm) and its release keeping the proven rule's lagging-arm buffer (THE LAGGING ARM AT THE RELEASE, verify r8
# policy MF1), and beyond it is placed with base motion; only a block no such plan serves keeps the long carries. Most
# scenes below were built on blocks the proven policy places (ARM_STAGE and its kin) to pin the long carry's own behaviour:
# the carry, the settle, the way back under load and the fallback after it. They run as LongCarryCase, with the cap function
# answering the reach model's cap and the release the admission's own margin (as_for_a_block_no_plan_under_the_cap_serves),
# which is what the long carry runs with; TheLoadedReachCap, TheRecordedStartsUnderTheRecordedLoadedSequence and
# TheRecordedStartsUnderTheLoadedLagInsideTheReach pin what the same starts, and the operator's block, do under the cap.
#
# What is pinned:
#   - the loaded reach cap: ARM_STAGE (a block 10.5 mm outside the near edge, grasped near, which the proven rule places
#     only with a 70 mm base advance) is placed by advance_then_reach's short advance and a carry inside the cap, the arm
#     alone refused; the starts of the long-carry scenes are placed wherever the run with no word places them, no additive
#     plan or loaded command past the cap; a proven tie at no base motion asks nothing at SEARCH and runs as the installed
#     controller runs it; among blocks the proven policy places, multi_block binds the block the installed rule binds;
#   - arm_reach, as for a block no plan under the cap serves: ARM_STAGE is placed by the arm alone with no base pulse, every
#     carry step stated with its commanded and measured out;
#   - an auto target bound to the arm alone whose in-run fallback moves the base, in the runtime's first spawn (no
#     capability): refused at SEARCH before anything is picked up, asking for the capability (mission_fallback_capability_
#     required, the selection the arm alone), a result the runtime's own reading answers with its one respawn carrying the
#     capability; there the arm alone places it with no base motion, claimed with no relayed command;
#   - arm_reach refused at the release, then advance_then_reach, IN THE RUN THAT CARRIES THE CAPABILITY (writer(True), the
#     runtime's respawn): the executor's planner refuses the lowering far out (refusing_far_lowerings), RELEASE_ADMISSION
#     refuses the block as too high, the arm, which realized its commands, comes back to the carry pose holding it by THE
#     WAY BACK UNDER LOAD (every step bounded, read, and planned from the measured tool: never out of it, never up at
#     extension), and the same run continues with advance_then_reach (a short measured advance through the capability, then
#     the reach) and places it; the gripper opens once, after an admitted release. The same refusal from a loaded arm that
#     lags its last command (sagging up to 2.5 deg past +100 mm out) holds it before any step (verify r7 safety MF1: its way
#     back used to be stepped from the last command, out of the measured tool and up, toward the far carry target);
#   - advance_then_reach refused at its release, then advance_then_reach asked again (its own re-plan with a further pulsed
#     advance, verify r7 policy MF2), which places the block with less base than the proven policy, the operator's block
#     included; both refused, the proven stage from where the measured advances ended, the whole advance of the run inside
#     the 120 mm budget and returned; and when the proven rule cannot place it inside what is left of the budget either, the
#     cube is held at the carry pose (reach_policies_exhausted_while_held), the refusal naming the policies it asked; a
#     further advance whose last pulse carries the run past the 120 mm budget (a base that slips, then moves) ends right
#     after that pulse with the cube held at the low scan pose, nothing further commanded (verify r9 safety MF1);
#   - a release refused on a LOWERED block (a creep at the hold check, a drooping lowering) holds the block where it stands
#     before any step, at every out: a lift sagging further as two recorded loaded raises at extension did, or realizing
#     the step with the errors it measures and the recorded growth, would take it under the pad's top (verify r6 safety MF1,
#     TheWayBackUnderALiftThatSagsFurther; inside the proven loaded reach, verify r10 safety MF1 and the lead's ruling of
#     2026-10-02, TheFallbackAfterARefusedReleaseInsideTheProvenLoadedReach), so the base-branch continuation the recorded
#     start's respawn and the drooping lowering used to reach is pinned on the unit harness only; inside the proven loaded
#     reach a release refused at the carry height, from an arm that stands where it was commanded, takes its way back and
#     goes on with the in-run fallback, which places the block;
#   - the same refusal in a LIVE run without a mission target (it never drives the base forward) ends with the cube held
#     and the arm brought back, never extended, the fallback that would move the base admitted and not taken, and the
#     refusal says why the arm alone did not place the block: it never says the reach fell short when the margin was met;
#   - THE WAY BACK UNDER LOAD (the lead's decision A of 2026-10-01, verify r5 safety MF1): a loaded arm that lags its last
#     command, one whose shoulder_lift already stands more than any loaded error on record from its target (the 4.5 deg sag
#     of a carry that fell short, the 3.9 deg re-send sag of a carry that sagged to the floor), or a held block the droop left
#     at the pad's top, is held where it stands, nothing sent, the gripper closed (reach_way_back_held); under the loaded lift the records show at
#     extension (a raise past +95 mm out not realized: stalled()) the way back ends held after at most one bounded step,
#     never in the executor's arrival abort, the held block never under the pad's top;
#   - a target that declares its mode (here, none) is never placed by these words: it is bound and run as the installed
#     controller runs it (the lead's decision of 2026-10-01; test_reach_policies_default_path.py pins such missions against
#     31c546c);
#   - a loaded arm that sags on BOTH lift joints (the recorded shape, each joint under the carry's joint-error stop) brings the
#     held block down to the release height while it still moves out: the carry ends at the step that measured the tool
#     under its floor, nothing further is sent outward, and the block is never lowered further toward the pad;
#   - the same arm sagging only once commanded past an onset the carry never crossed: the lowering is never commanded
#     farther out than the carry's last command nor past the plan's own lowering, so it never takes the tool under the
#     executor's floor there (verify r3 replay MUST-FIX 1); and a lowering that alone droops by the largest loaded errors on
#     record is asked high enough that it never ends the run in the executor's abort either;
#   - a run bound BASE_ADVANCE (advance_then_reach with a small advance at SEARCH, respawned carrying the capability) whose
#     measured grasp would let the arm alone place the block: it is placed with advance_then_reach's smallest pulsed advance,
#     the least motion such a run may execute, never handed to the proven stage's whole advance nor refused with the cube
#     held (a grasp nearer than SEARCH's planning grasp, which the stock World never closes on);
#   - advance_then_reach from a start the proven rule refuses: the operator's block of 2026-09-30 07:14Z (the proven rule
#     needs 124.7 mm against 120) is placed under the proven loaded reach with a measured advance inside the budget, every
#     loaded command inside +95 mm out (the operator's ruling of 2026-10-02, Q8), and the base comes back;
#   - multi_block: of two blocks off the pad that the proven policy places, the one the installed rule binds (the nearer
#     the pad) is bound, whatever base the other's plan names, and run as the installed path runs it (the lead's decision of
#     2026-10-04); of two blocks the proven policy refuses, the one placed with the least base motion is bound and placed
#     under the loaded reach cap, where without the word the nearer one is bound;
#   - several blocks off the pad (block_selection THE BINDING TIERS): beside a block the proven policy places, a block only
#     the long stretch places (by a side edge, past the capped plans' budget, or by a side edge of a pad seen 20 deg off its
#     axis; verify r11 safety MF1, policy MF1 and replay MUST-FIX 1), and a block the proven policy refuses that the words
#     plan under the loaded reach cap with less base (verify r12 safety MF1 and replay MUST-FIX 1, the lead's decision of
#     2026-10-03), are never bound under the words, by either binding rule, in either listing order, and the block the
#     installed path places is bound and placed wherever the run with no word places it, under the recorded loaded sequences
#     and lag (they bound the other block, and its carry ended held on the stretched arm); alone, each keeps its own plan;
#     where the proven policy places none of them, a block whose plan under the cap still fits at the record's largest grasp
#     under-read is bound before a nearer one whose plan fits only inside SEARCH's charge, and placed under the cap where the
#     nearest-the-pad rule bound the other, whose plan went long once held at the World's grasp; beside a block nearer the
#     pad that the proven policy places with the base, a block its proven plan places with the arm alone is never bound by
#     multi_block, whether that plan needs the base a few mm past the coarse grasp or not: the block the installed path binds
#     is bound and placed wherever the run with no word places it (verify r13 safety MF1 and policy MF1, verify r14 safety,
#     policy and gates MF1, the lead's decision of 2026-10-04: the arm-alone block ended held at the grasp, held where its
#     proven carry stopped under the recorded loaded sequences, or held lowered under the recorded loaded lag);
#   - a run without a mission target plans and executes the same policies (the World's own base-advance scene);
#   - under the recorded loaded sequence past +95 mm out (the two long loaded carries on record, send after send), every
#     recorded and committed start the installed path places is placed under every placement word set, no loaded move of
#     its run commanded past the proven loaded reach (verify r6 replay MUST-FIX 1, settled by the cap); the operator's block,
#     which the proven policy refuses, is planned under the cap too and placed, no loaded move past +95 mm out (verify r10
#     replay MUST-FIX 1, settled by the operator's ruling of 2026-10-02, Q8; it used to bind a carry past it);
#   - under the loaded lag the record shows INSIDE the proven loaded reach as well (loaded_lag: the descent's errors of the
#     2026-09-20 base placements on every loaded move, and those graded to the first send of a far carry), the starts the
#     installed path places are placed under the placement words, no loaded command past the proven loaded reach: their
#     plans count only on the out their capped commands measure under the record and keep the proven rule's lagging-arm
#     buffer (verify r8 policy MF1 and replay MUST-FIX 1: they ended with the cube held on the arm when a plan counted on
#     measuring +95 mm under commands capped there, with 0.5 to 3.5 mm of margin over the admission); the operator's block,
#     under the same cap (Q8), is placed under that lag as well;
#   - with no word none of this is reached (test_reach_policies_default_path.py pins the default path against 31c546c).
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_reach_policies_execution.py
import ast
import contextlib
import json
import math
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))
sys.path.insert(0, str(ROOT / "packages" / "demo-runtime"))

import run_single_cube_cycle as current  # noqa: E402
import placement_policy as pp  # noqa: E402
import reach_carry as rc  # noqa: E402
import reach_model  # noqa: E402
import target_binding as tb  # noqa: E402
import test_cycle_mission_mode as mission  # noqa: E402
import test_run_single_cube_cycle as fixtures  # noqa: E402  (the World's synthetic pad view)
from demo_runtime import mission_execution as runtime  # noqa: E402  (the runtime's own reading of a SEARCH refusal, never started here)

WORDS = {"KC_PLACEMENT_ENVELOPE": "arm_reach,advance_then_reach"}
AUTO = [mission.declared("cube", tb.AUTO)]
# A block 10.5 mm outside the near edge, seen where the coarse estimate puts it 10 mm beyond the proven grasp radius
# (contact row 545 + (11.4 - 10) x 2.7): arm_reach carries it with no base motion; the proven rule needs a 70 mm advance.
ARM_STAGE = {"pad_m": [0.0145, -0.158], "contact_px": [553.0, 545.0 + (11.4 - 10.0) * current.LOW_POSE_PX_PER_RADIAL_MM]}
LIVE = {"pad_m": [-0.035768012099579305, -0.2129612198129184], "contact_px": [454.8, 589.7]}
# Inside by 4 mm, grasped far out: the proven rule places it with a 55 mm advance, more than advance_then_reach needs for
# ARM_STAGE (inside by 15 mm it needed 44, less than ARM_STAGE once the loaded reach cap kept the record's lag and the
# lagging-arm buffer, verify r8 policy MF1).
NEAR = {"pad_m": [-0.08, -0.1475 + 0.004], "contact_px": [553.0, 545.0 - (60.0 - 11.4) * current.LOW_POSE_PX_PER_RADIAL_MM]}


class PhysicalWorld(mission.MissionWorld):
    """The scripted World (module comment): a block released where the arm and the base put it, and a loaded arm that sags
    past loaded_reach_out_mm. With creep_beyond_out_mm set, a loaded arm lowered to the release height farther out than
    that creeps creep_deg of shoulder_lift while it is held still for the hold check (the world's clock asked to sleep
    reach_carry.HOLD_CHECK_S), so the hold check's read sees it."""

    def __init__(self, **script):
        super().__init__(**{"loaded_reach_out_mm": None, "loaded_sag_max_deg": 4.5, "creep_beyond_out_mm": None, "creep_deg": 1.5, **script})
        self.grasp_out_mm, self.release_out_mm, self.lowered, self.crept = None, None, False, False
        world, sleep = self, self.clock.sleep

        def held_still(seconds):
            if world.lowered and not world.crept and abs(float(seconds) - rc.HOLD_CHECK_S) < 1e-9:
                world.joints["shoulder_lift"] += world.script["creep_deg"]  # held still far out, the loaded arm creeps down and back
                world.crept = True
            sleep(seconds)
        self.clock.sleep = held_still

    def holding(self) -> bool:
        return self.closing and self.was_held and not self.released

    def apply(self, action: dict) -> None:
        released_before = self.released
        closing = "arm_gripper.pos" in action and float(action["arm_gripper.pos"]) < self.joints["gripper"] - 1e-9  # a tick of a closing gripper move
        super().apply(action)
        if closing:
            self.grasp_out_mm = current.tool_state(self.joints)["out_mm"]  # the arm stands still while the jaws close
        limit = self.script["loaded_reach_out_mm"]
        if limit is not None and self.holding() and "arm_shoulder_lift.pos" in action:
            commanded = current.tool_state(self.joints)["out_mm"]
            if commanded > limit:
                self.joints["shoulder_lift"] += min((commanded - limit) / 3.3, self.script["loaded_sag_max_deg"])
        creep = self.script["creep_beyond_out_mm"]
        if creep is not None and self.holding() and "arm_shoulder_lift.pos" in action and not self.crept:
            tool = current.tool_state(self.joints)
            self.lowered = tool["up_mm"] < 20.0 and tool["out_mm"] > creep
        if self.released and not released_before:
            self.release_out_mm = current.tool_state(self.joints)["out_mm"]
            start = self.script["floor"][self.script["carried"]]["pad_m"]
            travel = self.base_mm + self.release_out_mm - self.grasp_out_mm
            self.script["cube_final_pad_m"] = [start[0], start[1] + travel / 1000.0]


class SaggingWorld(PhysicalWorld):
    """A loaded arm that, commanded past sag_from_out_mm, misses its target by lift_deg on shoulder_lift and elbow_deg on
    elbow_flex (both positive: the tool nearer and lower), the shape of the recorded loaded re-sends
    (cycle-run-20260920T093143Z: 3.54 and 2.67 deg), each under the carry's own joint-error stop."""

    def __init__(self, **script):
        super().__init__(**{"sag_from_out_mm": 95.0, "lift_deg": 3.9, "elbow_deg": 3.9, **script})

    def apply(self, action: dict) -> None:
        super().apply(action)
        if self.holding() and "arm_shoulder_lift.pos" in action:
            target = {**self.joints, "shoulder_lift": float(action["arm_shoulder_lift.pos"]), "elbow_flex": float(action["arm_elbow_flex.pos"])}
            if current.tool_state(target)["out_mm"] > self.script["sag_from_out_mm"]:
                self.joints["shoulder_lift"] = float(action["arm_shoulder_lift.pos"]) + self.script["lift_deg"]
                self.joints["elbow_flex"] = float(action["arm_elbow_flex.pos"]) + self.script["elbow_deg"]


class DroopingWorld(PhysicalWorld):
    """A loaded arm lowered toward the release height far out droops by the recorded first-send errors (cycle-run-
    20260920T093143Z: 1.69 deg on shoulder_lift, 2.85 on elbow_flex): the tool measures about 15 mm under the height asked
    and 13 mm nearer than the out. The World has no floor, so the held block measures under the pad's top, where on
    hardware the droop presses it onto the pad (verify r2 safety MF2)."""

    def __init__(self, **script):
        super().__init__(**{"lower_droop": (1.69, 2.85), **script})

    def apply(self, action: dict) -> None:
        super().apply(action)
        if self.holding() and "arm_shoulder_lift.pos" in action:
            target = {**self.joints, "shoulder_lift": float(action["arm_shoulder_lift.pos"]), "elbow_flex": float(action["arm_elbow_flex.pos"])}
            tool = current.tool_state(target)
            if tool["up_mm"] < 15.0 and tool["out_mm"] > 60.0:
                lift, elbow = self.script["lower_droop"]
                self.joints["shoulder_lift"] = float(action["arm_shoulder_lift.pos"]) + lift
                self.joints["elbow_flex"] = float(action["arm_elbow_flex.pos"]) + elbow


class TiltedPhysicalWorld(PhysicalWorld):
    """PhysicalWorld with its pad seen TILT_DEG off its axis (verify r11 policy MF1's second face): the default-path test's
    TiltedWorld turn, about a FIXED pivot (the pad point the reference pixel (553, 545) sees before any advance), so that a
    measured advance moves the seen pad along the turned axis, as a turned pad frame does on the robot. Every reach axis
    reads (sin 20 deg, cos 20 deg) in PAD_FRAME, and the released block travels along it."""

    TILT_DEG = -20.0

    def __init__(self, **script):
        super().__init__(**script)
        seen = np.asarray(fixtures._pad_view()["homography_image_to_pad"], dtype=float) @ np.array([553.0, 545.0, 1.0])
        self.pivot = (float(seen[0] / seen[2]), float(seen[1] / seen[2]))

    def turned(self, view: dict) -> dict:
        c, s = math.cos(math.radians(self.TILT_DEG)), math.sin(math.radians(self.TILT_DEG))
        px, py = self.pivot
        turn = np.array([[c, -s, px - c * px + s * py], [s, c, py - s * px - c * py], [0.0, 0.0, 1.0]])
        image_to_pad = turn @ np.asarray(view["homography_image_to_pad"], dtype=float)
        return {**view, "homography_image_to_pad": image_to_pad.tolist(), "homography_pad_to_image": np.linalg.inv(image_to_pad).tolist()}

    def perception(self) -> dict:
        record = super().perception()
        if record.get("pad_by_template"):
            record["pad_by_template"] = self.turned(record["pad_by_template"])
        return record

    def apply(self, action: dict) -> None:
        released_before = self.released
        super().apply(action)
        if self.released and not released_before:
            start = self.script["floor"][self.script["carried"]]["pad_m"]
            travel = (self.base_mm + self.release_out_mm - self.grasp_out_mm) / 1000.0
            along = (math.sin(math.radians(-self.TILT_DEG)), math.cos(math.radians(-self.TILT_DEG)))
            self.script["cube_final_pad_m"] = [start[0] + along[0] * travel, start[1] + along[1] * travel]


def stalled(base, onset_mm: float = 95.0):
    """`base` with the loaded lift the records show at extension (verify r5 safety MF1): every loaded raise of the
    shoulder_lift at +99.6 to +112.2 mm out on record (cycle-run-20260920T093143Z CARRY steps 3 to 5,
    cycle-run-20260920T103136Z CARRY step 3) asked 1.41 to 4.06 deg and realized -109% to 0%: the lift did not rise. While
    the gripper holds the block and the commanded tool is more than onset_mm out, a tick that commands the shoulder_lift
    lower than the tick before (a raise) is not realized by the lift, which keeps its value for the rest of that move (the
    next move starts from the measured pose, as the executor does); every other joint and tick is base's. `track` records
    the held tool (out, up) at every holding tick."""

    class Stalled(base):
        def __init__(self, **script):
            super().__init__(**script)
            self._last_lift, self._lag, self.track = None, 0.0, []

        def apply(self, action: dict) -> None:
            commanded = float(action["arm_shoulder_lift.pos"]) if "arm_shoulder_lift.pos" in action else None
            super().apply(action)
            if commanded is None:
                return
            raising = (self.holding() and self._last_lift is not None and commanded < self._last_lift - 1e-9
                       and current.tool_state({**self.joints, "shoulder_lift": commanded})["out_mm"] > onset_mm)
            self._lag = self._lag + (self._last_lift - commanded) if raising else 0.0
            self.joints["shoulder_lift"] += self._lag
            self._last_lift = commanded
            if self.holding():
                tool = current.tool_state(self.joints)
                self.track.append((round(tool["out_mm"], 1), round(tool["up_mm"], 1)))

    Stalled.__name__ = f"Stalled{base.__name__}"
    return Stalled


def sagging_further(base, growth_deg: float):
    """`base` with the loaded lift that two of the four recorded loaded raises at extension showed (verify r6 safety MF1):
    asked to rise, it sagged FURTHER from where it stood, +1.85 deg (cycle-run-20260920T093143Z CARRY step 3) and +1.49 deg
    (cycle-run-20260920T103136Z CARRY step 3). During every move of THE WAY BACK UNDER LOAD (a REACH_RETRACT label) the held
    shoulder_lift stands at its value when the move began plus growth_deg, whatever the ticks ask; every other joint follows
    its ticks. `track` records the held tool (out, up) at every such tick. The World knows a move's label only through
    run_pose, so run(case, ...) runs main() with Cycle.run_pose wrapped to say when a way-back move begins and ends."""

    class SaggingFurther(base):
        def __init__(self, **script):
            super().__init__(**script)
            self.sag_to, self.track = None, []

        def apply(self, action: dict) -> None:
            super().apply(action)
            if self.sag_to is not None and "arm_shoulder_lift.pos" in action and self.holding():
                self.joints["shoulder_lift"] = self.sag_to
                tool = current.tool_state(self.joints)
                self.track.append((round(tool["out_mm"], 1), round(tool["up_mm"], 1)))

        def run(self, case, writer_, env):
            world, original = self, current.Cycle.run_pose

            def run_pose(cycle, pose, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
                if label.startswith("REACH_RETRACT"):
                    world.sag_to = float(world.joints["shoulder_lift"]) + float(growth_deg)
                try:
                    return original(cycle, pose, holding, floor_m, pan_allowance, label, manipulation_step)
                finally:
                    world.sag_to = None

            with mock.patch.object(current.Cycle, "run_pose", run_pose):
                return case.mission(self, writer_, extra_env=env)

    SaggingFurther.__name__ = f"SaggingFurther{base.__name__}"
    return SaggingFurther


# The two long loaded carries on record, as the shoulder_lift and elbow_flex errors of each loaded send of one far target
# (measured minus commanded, deg): cycle-run-20260920T093143Z (first send, re-send, the sends after) and
# cycle-run-20260920T103136Z (first send, re-send). The controller's FK of the commanded targets with these errors gives
# the recorded measured outs and heights within 1.6 mm (verify r6 replay, its calibration table).
RECORDED_SEQUENCE_0931 = ((1.69, 2.85), (3.54, 2.67), (4.07, 2.67))
RECORDED_SEQUENCE_1031 = ((1.41, 2.95), (2.90, 2.95))


def recorded_loaded_sequence(base, sequence, onset_mm: float = 95.0):
    """`base` with the loaded arm the record shows past the proven loaded reach (verify r6 replay MUST-FIX 1): while the
    gripper holds the block, the n-th move whose target lies past onset_mm out misses its shoulder_lift and elbow_flex
    targets by the n-th errors of `sequence` (the last ones from then on), ramped over the move as its ticks go. The World
    learns a move's target only through run_pose, so run(case, ...) runs main() with Cycle.run_pose wrapped to say it.
    `track` records the held tool (out, up) at every tick of such a move."""

    class Recorded(base):
        def __init__(self, **script):
            super().__init__(**script)
            self.moves, self.move_target, self.track = 0, None, []
            self._loaded_move, self._loaded_index, self._start_lift = None, 0, None

        def apply(self, action: dict) -> None:
            super().apply(action)
            if self.move_target is None or not self.holding() or "arm_shoulder_lift.pos" not in action:
                return
            if current.tool_state({**self.joints, **self.move_target})["out_mm"] <= onset_mm:
                return
            if self._loaded_move != self.moves:
                self._loaded_move, self._loaded_index, self._start_lift = self.moves, self._loaded_index + 1, float(self.joints["shoulder_lift"])
            lift, elbow = sequence[min(self._loaded_index, len(sequence)) - 1]
            span = float(self.move_target["shoulder_lift"]) - self._start_lift
            progress = 1.0 if abs(span) < 0.05 else min(max((float(self.joints["shoulder_lift"]) - self._start_lift) / span, 0.0), 1.0)
            self.joints["shoulder_lift"] += lift * progress
            self.joints["elbow_flex"] += elbow * progress
            tool = current.tool_state(self.joints)
            self.track.append((round(tool["out_mm"], 1), round(tool["up_mm"], 1)))

        def run(self, case, writer_, env=None):
            world, original = self, current.Cycle.run_pose

            def run_pose(cycle, pose, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
                last = pose[-1] if isinstance(pose, (list, tuple)) else pose
                world.moves += 1
                world.move_target = {name: float(value) for name, value in last.joints.items() if name in world.joints and name != "gripper"}
                try:
                    return original(cycle, pose, holding, floor_m, pan_allowance, label, manipulation_step)
                finally:
                    world.move_target = None

            with mock.patch.object(current.Cycle, "run_pose", run_pose):
                return case.mission(self, writer_, extra_env=env)

    Recorded.__name__ = f"Recorded{base.__name__}"
    return Recorded


# The loaded arm INSIDE the proven loaded reach, as the evidence root records it (verify r8 policy MF1 and replay MUST-FIX 1):
# every loaded carry step of 2026-09-19/20 commanded inside +95 mm out measured 14.0 to 19.9 mm short of its out
# (cycle-run-20260920T103136Z: +93.3 commanded, +77.3 measured), and the four proven base placements of 2026-09-20
# (cycle-run-20260920T115018Z, T120226Z, T121517Z, T133924Z) missed every loaded move, their descent to the carry height by
# 0.56 to 0.79 deg of shoulder_lift and 1.80 to 2.23 of elbow_flex (13.1 to 15.8 mm short). Each is (lift, elbow, out) at two
# outs of the move's target, the errors interpolated between them and held outside: the descent's largest errors on every
# loaded move, and the same at +30 mm out graded to the first send of cycle-run-20260920T093143Z (1.69 and 2.85 deg at
# +116 mm out commanded), the verify r8 policy lens's graded model, anchored on both records.
LAG_RECORDED_INSIDE_THE_REACH = ((0.8, 2.2, 0.0), (0.8, 2.2, 1.0))
LAG_GRADED_TO_THE_FIRST_SEND = ((0.8, 2.2, 30.0), (1.69, 2.85, 116.0))


def loaded_lag(base, low, high):
    """`base` with the loaded arm the record shows inside the proven loaded reach too (verify r8 policy MF1): while the
    gripper holds the block, EVERY move misses its shoulder_lift and elbow_flex targets by the errors interpolated on its
    target's out between low = (lift, elbow, out) and high, held constant outside them, ramped over the move as its ticks go
    (a positive error: the tool nearer and lower). The World learns a move's target only through run_pose, so run(case, ...)
    runs main() with Cycle.run_pose wrapped to say it."""

    class Lagging(base):
        def __init__(self, **script):
            super().__init__(**script)
            self.moves, self.move_target, self._loaded_move, self._start_lift = 0, None, None, None

        def apply(self, action: dict) -> None:
            super().apply(action)
            if self.move_target is None or not self.holding() or "arm_shoulder_lift.pos" not in action:
                return
            out = current.tool_state({**self.joints, **self.move_target})["out_mm"]
            share = min(max((out - low[2]) / (high[2] - low[2]), 0.0), 1.0)
            lift, elbow = low[0] + share * (high[0] - low[0]), low[1] + share * (high[1] - low[1])
            if self._loaded_move != self.moves:
                self._loaded_move, self._start_lift = self.moves, float(self.joints["shoulder_lift"])
            span = float(self.move_target["shoulder_lift"]) - self._start_lift
            progress = 1.0 if abs(span) < 0.05 else min(max((float(self.joints["shoulder_lift"]) - self._start_lift) / span, 0.0), 1.0)
            self.joints["shoulder_lift"] += lift * progress
            self.joints["elbow_flex"] += elbow * progress

        def run(self, case, writer_, env=None):
            world, original = self, current.Cycle.run_pose

            def run_pose(cycle, pose, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
                last = pose[-1] if isinstance(pose, (list, tuple)) else pose
                world.moves += 1
                world.move_target = {name: float(value) for name, value in last.joints.items() if name in world.joints and name != "gripper"}
                try:
                    return original(cycle, pose, holding, floor_m, pan_allowance, label, manipulation_step)
                finally:
                    world.move_target = None

            with mock.patch.object(current.Cycle, "run_pose", run_pose):
                return case.mission(self, writer_, extra_env=env)

    Lagging.__name__ = f"Lagging{base.__name__}"
    return Lagging


def refusing_far_lowerings(base, beyond_out_mm: float):
    """`base` whose executor's planner refuses, before anything moves, every lowering commanded farther out than
    beyond_out_mm (as run_pose's own planner refuses a height far out whose sag margin it cannot keep: reach_carry, THE
    SETTLE). The block stays at the carry height, the release admission refuses it as too high, and an arm that realizes
    every command comes back from where its carry measured, the measured tool and the last command at one point: THE WAY BACK
    UNDER LOAD with no lag to plan around. The World learns a move's label and target only through run_pose, so run(case,
    ...) runs main() with Cycle.run_pose wrapped."""

    class Refusing(base):
        def refusals(self):
            """The window in which the planner refuses those lowerings (run wraps the mission in it; a LIVE run opens it)."""
            world, original = self, current.Cycle.run_pose

            def run_pose(cycle, pose, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
                last = pose[-1] if isinstance(pose, (list, tuple)) else pose
                target = current.tool_state({**world.joints, **{name: float(value) for name, value in last.joints.items() if name in world.joints and name != "gripper"}})
                if label == "PLACE lower" and target["out_mm"] > beyond_out_mm:
                    raise current.ScanRefused(f"scripted: the executor's planner keeps no sag margin for a lowering {target['out_mm']:.1f} mm out")
                return original(cycle, pose, holding, floor_m, pan_allowance, label, manipulation_step)

            return mock.patch.object(current.Cycle, "run_pose", run_pose)

        def run(self, case, writer_, env=None):
            with self.refusals():
                return case.mission(self, writer_, extra_env=env)

    Refusing.__name__ = f"RefusingFarLowerings{base.__name__}"
    return Refusing


def slipping_after_the_way_back(base, slips: int, slip_mm: float = 0.5):
    """`base` (a refusing_far_lowerings World) whose base slips on the first `slips` forward pulses after THE WAY BACK UNDER
    LOAD sent its first step (each pulse moves slip_mm: a slipping or stalled base), then moves as the World's base always
    moves: the further advance of advance_then_reach asked again (verify r9 safety MF1). The World learns the way back's
    steps by their run_pose label, as refusing_far_lowerings learns the lowering's."""

    class Slipping(base):
        def __init__(self, **script):
            super().__init__(**script)
            self.came_back, self.slipped, self.sent_at_forward_pulses = False, [], []

        @contextlib.contextmanager
        def refusals(self):
            with super().refusals():
                world, refusing = self, current.Cycle.run_pose

                def run_pose(cycle, pose, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
                    world.came_back = world.came_back or label.startswith("REACH_RETRACT")
                    return refusing(cycle, pose, holding, floor_m, pan_allowance, label, manipulation_step)

                with mock.patch.object(current.Cycle, "run_pose", run_pose):
                    yield

        def move_base(self, vx_mps, duration_s) -> bool:
            if vx_mps > 0:
                self.sent_at_forward_pulses.append(len(self.sent))
            if vx_mps > 0 and self.came_back and len(self.slipped) < slips:
                before = self.base_mm
                stopped = super().move_base(vx_mps, duration_s)
                self.base_mm = before + slip_mm
                self.slipped.append(round(duration_s, 3))
                return stopped
            return super().move_base(vx_mps, duration_s)

    Slipping.__name__ = f"Slipping{base.__name__}"
    return Slipping


def assert_way_back_from_the_measured_tool(case, run) -> int:
    """verify r7 safety MF1: every step THE WAY BACK UNDER LOAD sent was commanded no farther out than the tool measured
    before it, and, back toward the body, no more than WAY_BACK_SAG_MM above it. The number of steps."""
    steps = fields(run, "REACH_RETRACT_STEP")
    for step in steps:
        case.assertLessEqual(step["commanded_out_mm"], step["before_out_mm"] + 0.1, step)
        if step["phase"] == rc.BACK:
            case.assertLessEqual(step["commanded_up_mm"], step["before_up_mm"] + rc.WAY_BACK_SAG_MM + 0.1, step)
    return len(steps)


@contextlib.contextmanager
def as_for_a_block_no_plan_under_the_cap_serves():
    """THE LONG CARRY (the operator's rulings of 2026-10-01 and 2026-10-02, placement_policy THE LOADED REACH CAP and THE
    LAGGING ARM AT THE RELEASE): the cap function answers the reach model's measured-out cap for every block, and the release
    keeps the admission's own margin, as for a block the proven policy refuses and no plan of the run under the cap serves,
    so that a scene on a block the proven policy places runs the long carry the cap leaves to such blocks (module comment)."""
    with mock.patch.object(pp, "loaded_reach_cap_mm", lambda ctl, situation, proven_admits: reach_model.measured_out_cap_mm(ctl)), \
            mock.patch.object(pp, "release_lag_mm", lambda ctl, situation: 0.0):
        yield


class LongCarryCase(mission.MissionCase):
    """A scene of the long carry the loaded reach cap leaves to a block no plan under it serves
    (as_for_a_block_no_plan_under_the_cap_serves), on the geometry of a block the proven policy places; TheLoadedReachCap and
    TheRecordedStartsUnderTheRecordedLoadedSequence pin what the same starts do under the cap."""

    def setUp(self):
        super().setUp()
        self.enterContext(as_for_a_block_no_plan_under_the_cap_serves())


def loaded_commands(run) -> list:
    """The target of every loaded move an additive policy commanded (the carry, the lowering and the way back), as the
    executor's own PLAN lines state it: (label, the tool state it was commanded to)."""
    return [(label, target) for label, _start, target in planned_moves(run) if label.startswith(("REACH_CARRY", "PLACE lower", "REACH_RETRACT"))]


def planned_moves(run) -> list:
    """Every arm move of the run's stdout, in order: (label, the tool state it started from, the tool state it was commanded
    to), from the executor's own PLAN lines."""
    moves = []
    for line in run["stdout"].splitlines():
        if not line.startswith("PLAN ") or ": start " not in line or " -> target " not in line:
            continue
        label, rest = line[len("PLAN "):].split(": start ", 1)
        start, target = rest.split(" -> target ", 1)
        start, target = ast.literal_eval(start), ast.literal_eval(target)
        moves.append((label, current.tool_state({**start, **target, **start}), current.tool_state({**start, **target})))
    return moves


def writer(carry: bool):
    return lambda directory: mission.write_target(directory, "cube", AUTO, carry=carry)


def declared_arm_alone():
    """A target that declares no capability (requires_capability false): the runtime never gives its run the capability, and
    the additive words never apply to it (the lead's decision of 2026-10-01)."""
    return lambda directory: mission.write_target(directory, "cube", [mission.declared("cube", False)], carry=False)


LIVE_WORDS = {**WORDS, "KC_PLACEMENT_ENVELOPE_ATTENDED": "attended-reach-policies-test"}


def live_run(case, world, **options) -> dict:
    """A LIVE run without a mission target (KC_RUNTIME_MODE=live, the attended label the unqualified words need), as the
    console's own LIVE runs are wired: the base never moves forward (its observation would be the synthetic one), so the
    fallback that would move the base is never taken, and the run that owned the synthetically fed primitive cannot import
    it (TheSyntheticObservationIsOutOfALiveRun)."""
    with mock.patch.dict(sys.modules, {"run_hybrid_pick_place": None}):
        return mission.run_world(world, case.directory, real_move_base=True, runtime_mode=current.RUNTIME_MODE_LIVE, extra_env=LIVE_WORDS, **options)


def as_the_runtime_runs_it(case, make_world, env) -> tuple:
    """An auto target bound to the arm alone, as the runtime runs it: the first spawn, without the capability, asks for it at
    SEARCH before anything is picked up (mission_fallback_capability_required); the one respawn carries it. (the first
    spawn's run, the respawn's World, the respawn's run)."""
    _, first = case.mission(make_world(), writer(False), extra_env=env)
    case.assertEnded(first, 5)
    case.assertEqual((runtime.FALLBACK_CAPABILITY_REQUIRED, [], tb.MODE_ARM_ONLY), (first["result"]["reason_code"], first["sent"], first["result"]["selection"]["mode"]))
    world = make_world()
    _, respawn = case.mission(world, writer(True), extra_env=env)
    return first, world, respawn


def fields(run, state: str) -> list:
    return [entry for entry in run["transitions"] if entry["state"] == state]


def gripper_opened_at(run) -> list:
    """The indexes of the commands that opened the gripper after it held."""
    opened, held = [], False
    for index, action in enumerate(run["sent"]):
        value = action.get("arm_gripper.pos")
        if value is None:
            continue
        if float(value) < current.GRIPPER_OPEN - current.GRIPPER_OPEN_TOLERANCE:
            held = True
        elif held and float(value) >= current.GRIPPER_OPEN - current.GRIPPER_OPEN_TOLERANCE:
            opened.append(index)
            held = False
    return opened


class ArmReach(mission.MissionCase):
    def world(self, **script):
        return PhysicalWorld(floor=[dict(ARM_STAGE)], **script)

    def test_with_no_word_the_proven_rule_needs_the_capability_and_nothing_moves(self):
        _, run = self.mission(self.world(), writer(False))
        self.assertEnded(run, 5)
        self.assertEqual(("mission_capability_required", []), (run["result"]["reason_code"], run["sent"]))
        self.assertNotIn("REACH_CARRY", run["states"])

    def test_a_block_the_proven_policy_places_is_carried_only_inside_the_proven_loaded_reach(self):
        """The operator's ruling of 2026-10-01 ("cap at proven reach", placement_policy THE LOADED REACH CAP). The proven policy
        places this block with a 70 mm base advance, so no additive plan carries it past the proven loaded reach: the arm
        alone, which reached about +126 mm out without the cap, is refused, and the least motion is advance_then_reach's short
        advance with a carry inside the cap. As the runtime runs it: the first spawn asks for the capability (a BASE_ADVANCE
        binding, nothing picked up), and the respawn that carries it places the block, every loaded command inside the cap."""
        _, first = self.mission(self.world(), writer(False), extra_env=WORDS)
        self.assertEnded(first, 5)
        self.assertEqual(("mission_capability_required", []), (first["result"]["reason_code"], first["sent"]))
        selected = fields(first, "TARGET_SELECTED")[0]
        plans = {plan["policy"]: plan for plan in selected["placement_policies"]}
        self.assertFalse(plans["arm_reach"]["admitted"], "the arm alone is refused inside the cap")
        self.assertEqual(("advance_then_reach", "BASE_ADVANCE"), (selected["placement_policy"], selected["mode"]))
        self.assertLess(selected["placement_plan"]["base_advance_mm"], plans["proven"]["base_advance_mm"] - current.BASE_ADVANCE_TOLERANCE_MM)
        self.assertLessEqual(selected["placement_plan"]["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM)
        _, run = self.mission(self.world(), writer(True), extra_env=WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        self.assertEqual("advance_then_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])
        self.assertTrue(run["base_pulses"], "placed with base motion")
        commands = loaded_commands(run)
        self.assertTrue(commands)
        self.assertLessEqual(max(target["out_mm"] for _label, target in commands), current.ARM_LOADED_REACH_OUT_MM + 0.05)
        self.assertEqual(1, len(gripper_opened_at(run)))
        self.assertTrue(fields(run, "BASE_RETURNED")[0]["value"])

    def test_the_arm_alone_places_a_block_the_proven_policy_refuses_with_no_base_motion(self):
        """The long carry the cap leaves to a block no plan under it serves (as_for_a_block_no_plan_under_the_cap_serves), on
        this geometry. As the runtime runs it: the first spawn asks for the capability its fallback would need, and in the
        respawn that carries it the arm alone places the block, the base never moving."""
        self.enterContext(as_for_a_block_no_plan_under_the_cap_serves())
        _first, world, run = as_the_runtime_runs_it(self, self.world, WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        self.assertEqual([], run["base_pulses"])
        selected = fields(run, "TARGET_SELECTED")[0]
        self.assertEqual(("arm_reach", "ARM_ONLY", True), (selected["placement_policy"], selected["mode"], selected["placement_executes"]))
        chosen = fields(run, "PLACEMENT_POLICY")[0]
        self.assertEqual("arm_reach", chosen["policy"])
        steps = fields(run, "REACH_CARRY")
        self.assertTrue(steps)
        for step in steps:
            self.assertEqual({"commanded_out_mm", "measured_out_mm"}, {"commanded_out_mm", "measured_out_mm"} & set(step))
        self.assertEqual(rc.ARRIVED, fields(run, "REACH_CARRY_STOPPED")[0]["reason"])
        admission = fields(run, "RELEASE_ADMISSION")[-1]
        self.assertTrue(admission["admitted"])
        self.assertEqual(("arm_reach", 0.0), (admission["policy"], admission["base_advance_measured_mm"]))
        order = [run["states"].index(name) for name in ("TARGET_SELECTED", "OBJECT_SECURED", "PLACEMENT_POLICY", "PLACEMENT_PLAN", "REACH_CARRY", "RELEASE_HOLD", "RELEASE_ADMISSION",
                                                         "RELEASED", "PLACED")]
        self.assertEqual(sorted(order), order)
        placed = fields(run, "PLACED")[0]
        self.assertTrue(placed["value"] and placed["attributed"])
        self.assertEqual(1, len(gripper_opened_at(run)))
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])


class AdvanceThenReachWithNoAdvance(LongCarryCase):
    def test_a_plan_with_no_advance_is_carried_from_the_held_pose_and_the_base_never_moves(self):
        """advance_then_reach alone, and a block the arm alone places: its plan is the smallest advance, 0, and it carries
        the block as arm_reach does, with no rise to the low scan pose and no base pulse."""
        _first, world, run = as_the_runtime_runs_it(self, lambda: PhysicalWorld(floor=[dict(ARM_STAGE)]), {"KC_PLACEMENT_ENVELOPE": "advance_then_reach"})
        self.assertEnded(run, 0)
        selected = fields(run, "TARGET_SELECTED")[0]
        self.assertEqual(("advance_then_reach", "ARM_ONLY", 0.0), (selected["placement_policy"], selected["mode"], selected["placement_plan"]["base_advance_mm"]))
        self.assertEqual([], run["base_pulses"])
        self.assertNotIn("HOLD_OBSERVE", run["states"])
        self.assertNotIn("REACH_REPLAN", run["states"])
        self.assertEqual("advance_then_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])
        self.assertEqual("placed_new", run["result"]["outcome"])


class ArmReachFallsShortThenAdvanceThenReach(LongCarryCase):
    """The loaded arm never measures much past +100 mm out: the arm-only carry to about +114 falls short, the loaded arm
    sagging 4.5 deg past its limit. In the runtime's respawn, which carries the capability (writer(True)), the way back
    holds it there (the first test); the first spawn, which carries none, asks for the capability before anything is
    picked up (the second test). The fallback after a refused release is ArmReachRefusedAtTheReleaseAdmission's."""

    def world(self):
        return PhysicalWorld(floor=[dict(ARM_STAGE)], loaded_reach_out_mm=100.0)

    def test_in_a_run_that_carries_the_capability_the_way_back_holds_a_loaded_arm_that_sagged_past_the_recorded_envelope(self):
        """The lead's decision A of 2026-10-01. A shoulder_lift 4.5 deg off its target is more than any loaded error on
        record (4.07), and with the 1.85 deg the loaded error grew in one send on record any step of the way back could end
        past the executor's 5 deg arrival abort: nothing is sent, the arm holds the cube where the carry left it, the
        gripper closed, and no policy goes on with the loaded arm extended (it used to retract it in two 55 mm steps and go
        on with advance_then_reach)."""
        world = self.world()
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        self.assertEqual("ARM_ONLY", fields(run, "TARGET_SELECTED")[0]["mode"], "bound arm-only at SEARCH")
        self.assertNotEqual(rc.ARRIVED, fields(run, "REACH_CARRY_STOPPED")[0]["reason"])
        self.assertIn("ARM_ONLY_INSUFFICIENT", run["states"])
        self.assertNotIn("REACH_RETRACT_STEP", run["states"], "no step of the way back was sent")
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])
        self.assertEqual(([], []), (gripper_opened_at(run), run["base_pulses"]))
        self.assertTrue(world.holding())
        refused = [line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED")][-1]
        self.assertIn("arrival abort", refused)

    def test_the_first_spawn_without_the_capability_asks_for_it_and_picks_nothing_up(self):
        """Lead decision 3 on the runtime's path: nothing is picked up by a run that could not go on with the base should the
        arm alone fall short (it used to end held here, the fallback admitted and not taken)."""
        _, run = self.mission(self.world(), writer(False), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual((runtime.FALLBACK_CAPABILITY_REQUIRED, [], []), (run["result"]["reason_code"], run["sent"], run["base_pulses"]))
        self.assertNotIn("PREGRASP", run["states"])
        self.assertTrue(runtime._search_measured_capability(run["result"], 5))


class TheRefusalWithoutTheCapabilitySaysWhatHappened(LongCarryCase):
    """A run that never drives the base forward ends every arm_reach attempt the admission did not take with the cube held:
    a LIVE run without a mission target (LIVE_FORWARD_EXCLUDED here; an auto mission target bound to the arm alone asks for
    the capability at SEARCH when its fallback moves the base, TheRuntimesFirstSpawnOfAnArmAloneBinding, and a declared
    target never meets these words). Its sentence and its values say what refused the arm alone, from the run's own record
    (run.finished); test_placement_executor.py pins the mission's own refusal, reach_arm_only_insufficient_while_held."""

    def failure(self, world, hold_s=None, code=current.LIVE_FORWARD_EXCLUDED) -> tuple:
        events = os.path.join(self.directory, "refusal-events.jsonl")
        with mock.patch.object(rc, "HOLD_CHECK_S", hold_s or rc.HOLD_CHECK_S):
            run = live_run(self, world, events_path=events)
        self.assertEnded(run, 5)
        lines = [json.loads(line) for line in Path(events).read_text().splitlines() if line.strip()]
        finished = [line for line in lines if line["kind"] == "run.finished"][0]["payload"]
        self.assertEqual(code, finished["reason_code"])
        if code == current.LIVE_FORWARD_EXCLUDED:
            self.assertIn("PLACEMENT_FALLBACK", run["states"])
        return run, finished["failure"]["detail"], {item["name"]: item["value"] for item in finished["failure"]["measurements"]}

    # A block 7.5 mm outside the near edge, seen as ARM_STAGE is: arm_reach plans a travel under its cap, so the release has the
    # plan's aim past its smallest admitted travel. The lowering is asked where the carry arrived (reach_carry THE SETTLE,
    # THE OUT: an arm that realizes its command is not led past it), and a creep of 1 deg while it is held still leaves the
    # hold unsteady (the tool drops about 5 mm) with the margin at the travel it crept back to still met.
    CREEP_STAGE = {"pad_m": [0.0145, -0.155], "contact_px": list(ARM_STAGE["contact_px"])}

    def test_a_carry_that_arrived_and_a_hold_that_was_not_steady_is_never_called_a_reach_that_fell_short(self):
        """The crept block stands where a step back could take it under the pad's top should the loaded lift not move (or sag
        further, as on record), so the way back holds it there (verify r6 safety MF1): the record says why, never that the
        reach fell short."""
        run, sentence, values = self.failure(PhysicalWorld(floor=[dict(self.CREEP_STAGE)], creep_beyond_out_mm=122.0, creep_deg=1.0), hold_s=1.25,
                                             code="reach_way_back_held")
        self.assertEqual(rc.ARRIVED, fields(run, "REACH_CARRY_STOPPED")[0]["reason"])
        admission = fields(run, "RELEASE_ADMISSION")[0]
        self.assertGreaterEqual(admission["worst_case_centre_margin_mm"], admission["required_mm"], "the margin at the reached travel was met")
        self.assertFalse(admission["hold_steady"])
        self.assertEqual(rc.HELD_NO_STEP, values["held_by"])
        self.assertNotIn("falls short", sentence)
        self.assertIn("the pad's top", sentence)
        self.assertEqual([], gripper_opened_at(run))

    def test_a_run_that_never_drives_the_base_forward_never_takes_the_fallback_that_would_move_it(self):
        """The fallback of lead decision 3 is admitted (advance_then_reach and the proven policy both place the block by moving
        the base) and not taken; the refusal says so, with the arm brought back and the cube held. The release is refused as
        too high where the executor's planner refuses the lowering far out (ArmReachRefusedAtTheReleaseAdmission's scene), and
        the way back, planned from the measured tool, comes back from there."""
        world = refusing_far_lowerings(PhysicalWorld, 120.0)(floor=[dict(ARM_STAGE)])
        with world.refusals():
            run, sentence, values = self.failure(world)
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))
        self.assertEqual("release_too_high", values["refused_by"])
        self.assertNotIn("falls short", sentence)
        self.assertIn("REACH_RETRACT", run["states"], "the way back came back to the carry pose")
        (fallback,) = fields(run, "PLACEMENT_FALLBACK")
        self.assertEqual(("arm_reach", None, False), (fallback["from_policy"], fallback["to_policy"], fallback["may_drive_the_base"]))
        moving = {plan["policy"] for plan in fallback["candidates"] if plan["admitted"] and plan["base_advance_mm"] > 0.0}
        self.assertEqual({"advance_then_reach", "proven"}, moving)
        self.assertEqual([], run["base_pulses"])
        self.assertEqual([], gripper_opened_at(run))

    def test_a_carry_short_of_the_admission_is_never_lowered_and_the_way_back_holds_the_arm_the_sag_left_out(self):
        """The loaded arm sags 4.5 deg past +100 mm out (ArmReachFallsShortThenAdvanceThenReach): the carry falls short, the
        arm-only check refuses at the carry height, nothing is lowered, and the way back holds the arm where the sag left it
        (the lead's decision A of 2026-10-01): the record says why, and never that the hold was not steady."""
        events = os.path.join(self.directory, "refusal-events.jsonl")
        run = live_run(self, PhysicalWorld(floor=[dict(ARM_STAGE)], loaded_reach_out_mm=100.0), events_path=events)
        self.assertEnded(run, 5)
        finished = [json.loads(line) for line in Path(events).read_text().splitlines() if line.strip() and json.loads(line)["kind"] == "run.finished"][0]["payload"]
        self.assertEqual("reach_way_back_held", finished["reason_code"])
        self.assertIn("ARM_ONLY_INSUFFICIENT", run["states"])
        self.assertNotIn("RELEASE_ADMISSION", run["states"], "a carry short of the admission is never lowered for a release")
        self.assertNotIn("not steady", finished["failure"]["detail"])
        self.assertIn("the way back holds the loaded arm where it stands", finished["failure"]["detail"])
        values = {item["name"]: item["value"] for item in finished["failure"]["measurements"]}
        self.assertEqual(rc.HELD_NO_STEP, values["held_by"])
        # the lead's ruling of 2026-10-02 on operator question Q7 (keep the hold): the run's record says why the in-run
        # fallback did not run, in the sentence the console shows and in its values
        self.assertIn("the in-run fallback (the next placement policy, which moves the base and tries again) does not run: it starts only from the carry pose",
                      finished["failure"]["detail"])
        self.assertEqual("not_run", values["in_run_fallback"])


class ALoadedArmThatSagsToThePad(LongCarryCase):
    """Both lift joints miss by 3.9 deg past +95 mm commanded (under the 4 deg joint-error stop): the step that crosses it
    puts the held block a few mm above the grasp height, at the pad's top."""

    def carried(self, run) -> list:
        return [(entry["policy"], entry["step"], entry["measured_out_mm"], entry["up_mm"]) for entry in fields(run, "REACH_CARRY")]

    def assertStoppedAtTheFloor(self, run):
        steps = self.carried(run)
        stops = fields(run, "REACH_CARRY_STOPPED")
        self.assertEqual(rc.LOW, stops[0]["reason"])
        first = [step for step in steps if step[0] == stops[0]["policy"]][:stops[0]["steps"]]
        self.assertLess(first[-1][3], current.PLACE_HEIGHT_MM)
        self.assertTrue(all(step[3] >= current.PLACE_HEIGHT_MM for step in first[:-1]), first)
        self.assertEqual(stops[0]["steps"], len(first), "no carry step after the one that measured the tool under the floor")
        self.assertGreaterEqual(min(step[3] for step in steps), first[-1][3] - 1e-9, "no carry of the run went lower")
        self.assertFalse([line for line in run["stdout"].splitlines() if line.startswith("PLAN PLACE lower")], "the held block is never lowered further")

    def test_arm_reach_stops_at_the_floor_and_the_way_back_holds_the_block_where_the_sag_left_it(self):
        """With both lift joints 3.9 deg off and the 1.85 deg the loaded error grew in one send on record, any step of the
        way back could end past the executor's 5 deg arrival abort: nothing moves after the carry, the cube held (the
        lead's decision A of 2026-10-01; it used to raise the block straight up at the carry's far out)."""
        world = SaggingWorld(floor=[dict(ARM_STAGE)])
        run = live_run(self, world)
        self.assertEnded(run, 5)
        self.assertStoppedAtTheFloor(run)
        self.assertIn("ARM_ONLY_INSUFFICIENT", run["states"])
        self.assertNotIn("RELEASE_HOLD", run["states"], "never lowered, never held for a release")
        self.assertEqual([], gripper_opened_at(run))
        self.assertNotIn("REACH_RETRACT_STEP", run["states"])
        self.assertTrue([line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED") and "the way back holds the loaded arm" in line])
        self.assertTrue(world.holding())

    def test_the_operators_block_after_its_advance_stops_at_the_floor(self):
        world = SaggingWorld(floor=[dict(LIVE)])
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertStoppedAtTheFloor(run)
        self.assertEqual([], gripper_opened_at(run))


class TheLoweringNeverGoesWhereTheCarryDidNotCommand(LongCarryCase):
    """verify r3 replay MUST-FIX 1. The settle lowered the held block 6 mm past the carry's MEASURED out (and, after a first
    send that overshot a short plan by its 18 mm lead, 24 mm past the plan) to the release height, with no predicted-height
    check. SaggingWorld's loaded arm misses both lift joints by 3.9 deg once commanded past its onset: a lowering commanded
    past the carry's last command crossed the onset where no carry step had, the executor measured the tool link under its
    contact floor and stopped all motion, and the run ended aborted with the loaded arm extended. That lost recorded starts
    the proven path places under the same physics (the World as shipped, onset 95, and onsets across the recorded loaded
    sag). Now the lowering is never commanded farther out than the carry's last command nor past the plan's own lowering,
    and a lowering height predicted under the executor's floor (THE CARRY's own prediction, each lift error at least the
    largest loaded error on record) is never sent."""

    # Recorded starts: the PAD_FRAME centre and the contact pixel of their SEARCH view.
    START_9CE42C3F = {"pad_m": [-0.0122, -0.1574], "contact_px": [546.2, 541.6]}  # console-20260921T220603Z-9ce42c3f, 9.9 mm outside the near edge
    START_ROW8 = {"pad_m": [0.0113, -0.1681], "contact_px": [586.7, 556.0]}  # cycle-run-20260920T103136Z, placed arm-only on hardware (21.4 mm)

    def assertPlacedWithEveryLoweringInsideItsCarry(self, run):
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        self.assertTrue(fields(run, "RELEASE_ADMISSION")[-1]["admitted"])
        self.assertEqual(1, len(gripper_opened_at(run)))
        moves = planned_moves(run)
        lowerings = [(moves[index - 1], target) for index, (label, _start, target) in enumerate(moves) if label == "PLACE lower" and index
                     and moves[index - 1][0].startswith("REACH_CARRY")]
        self.assertTrue(lowerings, "an additive policy lowered the block after its carry")
        for (label, _start, carried_to), lowered in lowerings:
            self.assertLessEqual(lowered["out_mm"], carried_to["out_mm"] + 0.1, f"the lowering after {label} goes past it")

    def test_the_sagging_world_as_shipped_never_lowers_past_the_carry_and_holds_the_arm_the_sag_left_out(self):
        """console-20260921T220603Z-9ce42c3f. With no word the proven path places it under the same physics. Under the words
        arm_reach's carry sags to its floor (both lift joints 3.9 deg off): nothing is lowered, and with the 1.85 deg the
        loaded error grew in one send on record no step of the way back stays short of the executor's arrival abort, so the
        arm holds the cube where the sag left it (the lead's decision A of 2026-10-01): the run ends for the operator, never
        in the abort. It used to retract in 55 mm steps and be placed by advance_then_reach, whose lowering then went past
        its plan (this class's MUST-FIX, pinned by the tests below)."""
        _, proven = self.mission(SaggingWorld(floor=[dict(self.START_9CE42C3F)]), writer(True))
        self.assertEnded(proven, 0)
        self.assertEqual("placed_new", proven["result"]["outcome"], "with no word the proven path places it under the same physics")
        _first, world, run = as_the_runtime_runs_it(self, lambda: SaggingWorld(floor=[dict(self.START_9CE42C3F)]), WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        self.assertEqual(rc.LOW, fields(run, "REACH_CARRY_STOPPED")[0]["reason"])
        self.assertFalse([label for label, _start, _target in planned_moves(run) if label == "PLACE lower"], "nothing lowered")
        self.assertNotIn("REACH_RETRACT_STEP", run["states"])
        self.assertEqual(([], []), (gripper_opened_at(run), run["base_pulses"]))
        self.assertTrue(world.holding())

    def test_a_carry_that_arrived_where_it_was_commanded_is_lowered_there(self):
        """cycle-run-20260920T103136Z, with the sag onset at 115 mm commanded: advance_then_reach's carry arrives at its planned
        113.5 mm, exactly where it was commanded; the lowering went to 119.5 mm, over the onset."""
        _, run = self.mission(SaggingWorld(floor=[dict(self.START_ROW8)], sag_from_out_mm=115.0), writer(True), extra_env=WORDS)
        self.assertPlacedWithEveryLoweringInsideItsCarry(run)
        self.assertEqual("advance_then_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])

    def test_the_arm_alone_at_its_reach_cap_is_lowered_where_its_carry_was_commanded(self):
        """The builder's arm-alone start, with the sag onset at 130 mm commanded: arm_reach arrives at its 126 mm cap; the
        lowering went to 132 mm, over the onset."""
        _, run = self.mission(SaggingWorld(floor=[dict(ARM_STAGE)], sag_from_out_mm=130.0), writer(True), extra_env=WORDS)
        self.assertPlacedWithEveryLoweringInsideItsCarry(run)
        self.assertEqual("arm_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])

    def test_the_operators_block_is_never_left_on_an_extended_arm(self):
        """The operator's block of 2026-09-30 07:14Z, with the sag onset at 120 mm commanded: advance_then_reach arrives
        after its measured advance and the lowering went past the onset."""
        _, run = self.mission(SaggingWorld(floor=[dict(LIVE)], sag_from_out_mm=120.0), writer(True), extra_env=WORDS)
        self.assertPlacedWithEveryLoweringInsideItsCarry(run)
        self.assertEqual("advance_then_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])

    def test_a_lowering_that_droops_by_the_largest_loaded_errors_on_record_never_ends_the_run_in_the_executors_abort(self):
        """The lowering alone droops (DroopingWorld: the carry before it measured no error) by the largest loaded errors on
        record: 4.07 deg of shoulder_lift (cycle-run-20260920T093143Z's re-sends) with its elbow_flex 2.41 deg, and with the
        largest elbow_flex error, 2.95 deg (cycle-run-20260920T103136Z). Lowered to the proven release height far out, the
        tool went under the executor's floor and the executor aborted with the loaded arm extended. Every lowering height
        is now predicted with at least these errors, so the lowering is never aborted; the drooped block, at the pad, is
        refused and held where it stands (the way back sends nothing from there: the lead's decision A of 2026-10-01), the
        run ending for the operator, never in the abort."""
        for block, droop in ((self.START_ROW8, (4.07, 2.41)), (ARM_STAGE, (4.07, 2.95))):
            with self.subTest(block=block["pad_m"], droop=droop):
                world = DroopingWorld(floor=[dict(block)], lower_droop=droop)
                _, run = self.mission(world, writer(True), extra_env=WORDS)
                self.assertEnded(run, 5)
                self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
                for hold in fields(run, "RELEASE_HOLD"):
                    self.assertGreaterEqual(hold["lower_predicted_lowest_link_m"], current.CONTACT_FLOOR_M)
                self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1], "nothing moves after the drooped lowering")
                self.assertEqual([], gripper_opened_at(run))
                self.assertTrue(world.holding())


class ArmReachRefusedAtTheReleaseAdmission(LongCarryCase):
    """The carry arrives, but the RELEASE ADMISSION refuses, the arm comes back holding the block (THE WAY BACK UNDER LOAD,
    every step bounded, read, and planned from where the tool measures) and, in a run that carries the capability
    (writer(True), which the runtime's first spawn of a block bound to arm_reach does not), the same run goes on with
    advance_then_reach. That spawn's own ending is TheRefusalWithoutTheCapabilitySaysWhatHappened's; a loaded arm that lags
    its last command, or a refused release that left the block where a step back could take it under the pad's top, is held
    where it stands (below; TheWayBackUnderTheRecordedLoadedLift, TheWayBackUnderALiftThatSagsFurther)."""

    def test_in_a_run_that_carries_the_capability_the_release_admission_refuses_and_advance_then_reach_places_it(self):
        """The executor's planner refuses every lowering past +120 mm out (refusing_far_lowerings): arm_reach's block stays
        at the carry height and the admission refuses it as too high. The arm realized its commands, so the way back,
        planned from the measured tool, goes back at the carry height and comes back; advance_then_reach's lowering after
        its measured advance lies nearer and is taken."""
        world = refusing_far_lowerings(PhysicalWorld, 120.0)(floor=[dict(ARM_STAGE)])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 0)
        self.assertIn("PLACE_LOWER_REFUSED", run["states"])
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))
        self.assertEqual(rc.ARRIVED, fields(run, "REACH_CARRY_STOPPED")[0]["reason"])
        first = fields(run, "RELEASE_ADMISSION")[0]
        self.assertEqual(("arm_reach", False, True), (first["policy"], first["admitted"], first["hold_steady"]))
        self.assertGreater(first["release_height_mm"], current.RELEASE_MAX_HEIGHT_MM)
        self.assertLess(run["states"].index("RELEASE_ADMISSION"), run["states"].index("REACH_RETRACT"))
        steps = fields(run, "REACH_RETRACT_STEP")
        self.assertTrue(steps)
        self.assertTrue(all(abs(step["shoulder_lift_change_deg"]) <= rc.WAY_BACK_LIFT_STEP_MAX_DEG for step in steps))
        phases = [step["phase"] for step in steps]
        self.assertEqual(sorted(phases, key=(rc.BACK, rc.UP).index), phases, "back before up")
        fallback = fields(run, "PLACEMENT_FALLBACK")[0]
        self.assertEqual(("arm_reach", "advance_then_reach"), (fallback["from_policy"], fallback["to_policy"]))
        last = fields(run, "RELEASE_ADMISSION")[-1]
        self.assertEqual(("advance_then_reach", True), (last["policy"], last["admitted"]))
        self.assertEqual(1, len(gripper_opened_at(run)))
        self.assertEqual("placed_new", run["result"]["outcome"])
        self.assertTrue(fields(run, "BASE_RETURNED")[0]["value"])

    def test_a_loaded_arm_that_lags_its_carry_commands_is_held_before_any_step(self):
        """verify r7 safety MF1, on the scene this class used to place: a loaded arm that sags up to 2.5 deg past +100 mm out,
        its block left at the carry height (no lowering predicted clear of the executor's floor) and refused as too high.
        Planned from the last command, the way back's first step commanded the loaded arm about 7 mm OUT of the measured tool
        and 12 mm UP, toward the far carry target, and the World realized that raise at extension, which no record shows
        (the recorded loaded re-sends at extension sagged further). Planned from the measured tool, no step is inside the
        records: the arm holds the cube where the carry left it, nothing sent after the carry, the gripper closed."""
        world = PhysicalWorld(floor=[dict(ARM_STAGE)], loaded_reach_out_mm=100.0, loaded_sag_max_deg=2.5)
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        first = fields(run, "RELEASE_ADMISSION")[0]
        self.assertEqual(("arm_reach", False), (first["policy"], first["admitted"]))
        self.assertGreater(first["release_height_mm"], current.RELEASE_MAX_HEIGHT_MM)
        self.assertNotIn("REACH_RETRACT_STEP", run["states"])
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding())


class ARefusedReleaseAfterADroopingLowering(LongCarryCase):
    """The carry arrives; lowered with the recorded first-send droop, the held block measures under the pad's top and short
    of the travel the admission needs, which refuses it. The retract then went back and up in one move from there, dragging
    the block along the pad (verify r2 safety MF2); the rise that replaced it moved the block back and down under a lift that
    does not rise (verify r5 safety MF1). Now nothing is sent: any step back or up with a loaded lift that does not move
    would press the block further or drag it, and the way back holds the arm where it stands, the cube held."""

    def test_the_held_block_at_the_pad_is_never_moved_and_the_arm_holds_where_it_stands(self):
        world = DroopingWorld(floor=[dict(ARM_STAGE)])
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        first = fields(run, "RELEASE_ADMISSION")[0]
        self.assertEqual(("arm_reach", False), (first["policy"], first["admitted"]))
        self.assertLess(first["release_height_mm"], rc.PAD_THICKNESS_MM, "the droop took the held block under the pad's top")
        labels = [label for label, _start, _target in planned_moves(run)]
        self.assertEqual("PLACE lower", labels[-1], "nothing moves after the lowering")
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding())


def assert_fallback_stated_as_conditional(case, sentence: str) -> None:
    """verify r6 policy MF2. The way back holds a loaded arm it cannot bring back inside the recorded envelope (the lead's
    decision A of 2026-10-01), so the respawn that carries the capability goes on with the base only when the loaded arm
    came back to the carry pose (ArmReachFallsShortThenAdvanceThenReach's respawn holds the arm instead), and the advance a
    first spawn names is SEARCH's plan, planned again once the cube is held. Its refusal never states that fallback as a
    fact."""
    case.assertNotIn("if its release is refused the same run goes on", sentence)
    case.assertIn("only if the loaded arm comes back to the carry pose", sentence)
    case.assertIn("otherwise the arm holds the cube where it stands for the operator", sentence)
    case.assertIn("planned now from SEARCH and planned again", sentence)


class TheRuntimesFirstSpawnOfAnArmAloneBinding(LongCarryCase):
    """Lead decision 3 on the runtime's own path (verify r2 policy MF1, replay MUST-FIX 1; operator question Q1, option A):
    the runtime spawns an auto target's first execution without the capability and gives it only before a spawn (D1), so a
    block bound to the arm alone whose in-run fallback moves the base is refused at SEARCH, before anything is picked up,
    asking for the capability (mission_fallback_capability_required, the selection the arm alone); the runtime's one respawn
    carries it, and there the arm alone places the block with no base motion, or, when its release is refused and the way
    back brings the loaded arm to the carry pose, the same run goes on with the base; a loaded arm the way back cannot bring
    back is held where it stands for the operator (ArmReachFallsShortThenAdvanceThenReach). The refusal says so
    (assert_fallback_stated_as_conditional)."""

    def test_the_first_spawn_asks_for_the_capability_before_anything_is_picked_up(self):
        world = PhysicalWorld(floor=[dict(ARM_STAGE)])
        _, run = self.mission(world, writer(False), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual(([], []), (run["sent"], run["base_pulses"]), "nothing moved")
        self.assertNotIn("PREGRASP", run["states"])
        result = run["result"]
        self.assertEqual(("finished", "failed", runtime.FALLBACK_CAPABILITY_REQUIRED), (result["stage"], result["outcome"], result["reason_code"]))
        self.assertEqual((runtime.SEARCH_REFUSAL_FINAL_STATE, tb.MODE_ARM_ONLY), (result["final_state"], result["selection"]["mode"]))
        self.assertEqual({"object_secured": None, "placed": None, "base_returned": None}, result["predicates"])
        self.assertTrue(runtime._search_measured_capability(result, 5), "the runtime reads it as the measuring run's refusal and respawns once, carrying the capability")
        selected = fields(run, "TARGET_SELECTED")[0]
        self.assertEqual(("arm_reach", tb.MODE_ARM_ONLY), (selected["placement_policy"], selected["mode"]))
        refused = [line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED")]
        self.assertTrue(refused and "capability" in refused[-1], refused)
        assert_fallback_stated_as_conditional(self, refused[-1])

    def test_the_respawn_carrying_it_places_the_block_with_the_arm_alone_and_claims_it_with_no_relay(self):
        world = PhysicalWorld(floor=[dict(ARM_STAGE)])
        target, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 0)
        self.assertEqual(("placed_new", tb.MODE_ARM_ONLY), (run["result"]["outcome"], run["result"]["selection"]["mode"]))
        self.assertEqual([], run["base_pulses"], "the base never moved")
        self.assertTrue(tb.result_claims_placement(run["result"], target), "an arm-alone selection is claimed with no relayed command")
        self.assertEqual("arm_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])

    def test_a_target_that_declares_no_capability_is_never_asked_for_it_nor_bound_by_the_words(self):
        """The words apply to auto targets only (the lead's decision of 2026-10-01): a target that declares no capability is
        bound as the installed controller binds it. The proven rule places this block only with the base, so it has no
        candidate of its mode, and the run is refused at SEARCH under the words exactly as with none: nothing moves, no
        capability is asked for, and arm_reach, which would place it with the arm alone, is never planned."""
        _, plain = self.mission(PhysicalWorld(floor=[dict(ARM_STAGE)]), declared_arm_alone())
        _, run = self.mission(PhysicalWorld(floor=[dict(ARM_STAGE)]), declared_arm_alone(), extra_env=WORDS)
        for ended in (plain, run):
            self.assertEnded(ended, 5)
            self.assertEqual((tb.REFUSED_NO_CANDIDATE, [], []), (ended["result"]["reason_code"], ended["sent"], ended["base_pulses"]))
        self.assertEqual([], [entry["state"] for entry in run["transitions"] if "placement_policy" in entry], "no state of the run names a placement policy")


class AProvenTieBinding(mission.MissionCase):
    """verify r4 policy MF1 (lead decision 3 on the default AUTO path), under the operator's ruling of 2026-10-01 ("cap at
    proven reach", placement_policy THE LOADED REACH CAP). At SEARCH a tie at no base motion goes to the proven arm-alone
    plan. Without the cap the executor, planning again from the measured grasp, could hand the held cube to arm_reach where
    the proven plan needed the base there, so SEARCH asked such a binding for the capability. The proven policy placed these
    blocks at SEARCH, so their plans carry the cap, and no additive arm-alone plan reaches past the proven one: nothing is
    handed to the arm alone, SEARCH asks nothing, and the run is the installed controller's (the proven stage answers in a
    run without the capability; in a run that carries it, the least motion places the block with the base, inside the cap).
    The World closes the jaws 26.2 mm beyond the coarse grasp (0 here)."""

    EDGE = {"pad_m": [0.0145, -0.1525], "contact_px": [553.0, 545.0 + 11.4 * current.LOW_POSE_PX_PER_RADIAL_MM]}  # 5 mm outside the near edge: the proven plan needs the base at +22
    BORDER = {"pad_m": [0.0145, -0.1480], "contact_px": list(EDGE["contact_px"])}  # 0.5 mm outside: the arm alone at +22, the base at the World's +26.2

    def test_the_first_spawn_asks_nothing_and_is_the_installed_controllers_run(self):
        _, plain = self.mission(PhysicalWorld(floor=[dict(self.EDGE)]), writer(False))
        _, run = self.mission(PhysicalWorld(floor=[dict(self.EDGE)]), writer(False), extra_env=WORDS)
        for ended in (plain, run):
            self.assertEnded(ended, 5)
            self.assertEqual("mission_mode_changed_while_held", ended["result"]["reason_code"])
        selected = fields(run, "TARGET_SELECTED")[0]
        self.assertEqual(("proven", tb.MODE_ARM_ONLY), (selected["placement_policy"], selected["mode"]), "the tie went to the proven policy")
        self.assertNotIn(runtime.FALLBACK_CAPABILITY_REQUIRED, run["stdout"], "SEARCH asks nothing")
        chosen = fields(run, "PLACEMENT_POLICY")[0]
        self.assertEqual(("proven", ["proven"]), (chosen["policy"], chosen["eligible"]))
        self.assertFalse([plan for plan in chosen["policies"] if plan["policy"] == "arm_reach"][0]["admitted"], "no arm-alone plan inside the cap")
        self.assertEqual(plain["sent"], run["sent"], "the proven stage's own commands")

    def test_a_run_that_carries_the_capability_places_it_with_a_short_advance_inside_the_cap(self):
        _, run = self.mission(PhysicalWorld(floor=[dict(self.EDGE)]), writer(True), extra_env=WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        chosen = fields(run, "PLACEMENT_POLICY")[0]
        plans = {plan["policy"]: plan for plan in chosen["policies"]}
        self.assertEqual(("advance_then_reach", pp.MODE_BASE_ADVANCE), (chosen["policy"], chosen["plan"]["mode"]), "the least motion once held, with the base")
        self.assertLess(chosen["plan"]["base_advance_mm"], plans["proven"]["base_advance_mm"])
        self.assertLessEqual(max(target["out_mm"] for _label, target in loaded_commands(run)), current.ARM_LOADED_REACH_OUT_MM + 0.05)
        self.assertTrue(run["base_pulses"])

    def test_a_run_without_the_capability_never_hands_a_proven_binding_to_an_additive_policy(self):
        """The proven plan still places the block with the arm alone at SEARCH's planning grasp, so SEARCH asks nothing; the
        World's grasp lies beyond the recorded under-read, the proven plan needs the base there, and the run cannot move the
        base: the proven stage answers exactly as with no word, the cube held at the grasp."""
        _, plain = self.mission(PhysicalWorld(floor=[dict(self.BORDER)]), writer(False))
        _, worded = self.mission(PhysicalWorld(floor=[dict(self.BORDER)]), writer(False), extra_env=WORDS)
        self.assertEnded(plain, 5)
        self.assertEnded(worded, 5)
        self.assertEqual(("mission_mode_changed_while_held", "mission_mode_changed_while_held"), (plain["result"]["reason_code"], worded["result"]["reason_code"]))
        self.assertEqual("proven", fields(worded, "TARGET_SELECTED")[0]["placement_policy"])
        chosen = fields(worded, "PLACEMENT_POLICY")[0]
        self.assertEqual(("proven", ["proven"], False), (chosen["policy"], chosen["eligible"], chosen["may_drive_the_base"]))
        self.assertNotIn("REACH_CARRY", worded["states"])
        self.assertEqual(plain["sent"], worded["sent"], "the proven stage's own commands")


class TheWayBackUnderTheRecordedLoadedLift(LongCarryCase):
    """verify r5 safety MF1 (the lead's decision A of 2026-10-01). Under the loaded lift the records show at extension (a
    raise past +95 mm out is not realized, stalled()), the way back of a refused release used to end in the executor's
    arrival abort with the loaded arm extended, after the rise had moved the held block back and down under the grasp
    height (pressed onto the pad and dragged). Now every move of the way back stays inside the recorded envelope: the run
    ends with the arm held where the way back's bounds stopped it (reach_way_back_held), never in the abort, the gripper
    closed, the held block never under the pad's top, and every move bounded."""

    def assertHeldInsideTheEnvelope(self, run, world, start_state: str):
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"], run["stdout"][-2000:])
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding(), "the block is still in the gripper")
        planned = planned_moves(run)
        moves = [(label, start, target) for label, start, target in planned if label.startswith("REACH_RETRACT")]
        for index, (label, _start, target) in enumerate(planned):
            if label.startswith("REACH_RETRACT"):  # never farther out than the last command, never a re-send of the far carry target
                self.assertLessEqual(target["out_mm"], planned[index - 1][2]["out_mm"] + 0.05, f"{label} commanded the loaded arm farther out than its last command")
        lift = [line for line in run["stdout"].splitlines() if line.startswith("PLAN REACH_RETRACT")]
        for line in lift:
            start, target = (ast.literal_eval(part) for part in line.split(": start ", 1)[1].split(" -> target ", 1))
            self.assertLessEqual(abs(target["shoulder_lift"] - start["shoulder_lift"]), rc.WAY_BACK_LIFT_STEP_MAX_DEG + 1e-9, line[:120])
        index = run["states"].index(start_state)
        self.assertNotIn("RELEASED", run["states"][index:])
        return moves

    def test_a_refused_release_far_out_holds_the_lowered_block_before_any_step(self):
        """The creep scene (the release refused on an unsteady hold at about +122 mm out, the block about 7 mm up): its first
        step back used to be sent and, under this stalled lift, held the arm after it; should the lift sag further as two
        recorded loaded raises at extension did, that step ended the block under the pad's top (verify r6 safety MF1). Now
        nothing is sent and the arm holds where it stands: from the crept block, planned from where the tool measures, even
        the smallest step would take it under the pad's top should the loaded lift not move. A way back that takes one
        bounded step under a stalled lift and then holds is pinned on the unit harness (test_reach_carry, TheWayBackUnderLoad)."""
        world = stalled(PhysicalWorld)(floor=[dict(ARM_STAGE)], creep_beyond_out_mm=122.0, creep_deg=1.0)
        with mock.patch.object(rc, "HOLD_CHECK_S", 1.25):
            _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEqual([], self.assertHeldInsideTheEnvelope(run, world, "RELEASE_ADMISSION"))
        released = run["states"].index("RELEASE_ADMISSION")
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"][released:], "nothing goes on with the loaded arm extended")
        self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1])
        refused = [line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED")][-1]
        self.assertIn("the pad's top", refused)

    def test_a_refused_release_that_left_the_block_within_reach_of_the_pads_top_sends_nothing(self):
        """The same scene with the creep of 1.5 deg the World uses by default: the block measures about 5 mm up, and even
        the smallest step back would leave it under the pad's top should the loaded lift not move. Nothing is sent."""
        world = stalled(PhysicalWorld)(floor=[dict(ARM_STAGE)], creep_beyond_out_mm=122.0)
        with mock.patch.object(rc, "HOLD_CHECK_S", 1.25):
            _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEqual([], self.assertHeldInsideTheEnvelope(run, world, "RELEASE_ADMISSION"))
        self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1])

    def test_a_block_the_droop_left_under_the_pads_top_is_not_moved(self):
        """The committed rise scene (DroopingWorld, ARM_STAGE): lowered with the recorded first-send droop, the held block
        measures under the pad's top; nothing is sent after the refused release."""
        world = stalled(DroopingWorld)(floor=[dict(ARM_STAGE)])
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        moves = self.assertHeldInsideTheEnvelope(run, world, "RELEASE_ADMISSION")
        self.assertEqual([], moves)
        self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1])


class TheWayBackUnderALiftThatSagsFurther(LongCarryCase):
    """verify r6 safety MF1. Two of the four recorded loaded raises at extension sagged FURTHER from where the lift stood
    (+1.85 and +1.49 deg). Under that realization the first step back from the committed creep scene's lowered block
    (the release refused on an unsteady hold at about +122 mm out, the block about 7 mm up) ended 4.8 and 6.4 mm under the
    pad's top after 8 to 10 mm back: the held block pressed into the pad and dragged before the hold. No step is now sent
    whose held block is predicted under the pad's top under that realization, and the arm holds where it stands, the cube
    held, the gripper closed (the lead's decision A)."""

    def test_the_creep_scene_holds_the_lowered_block_and_never_presses_it_into_the_pad(self):
        for growth in (1.49, 1.85):
            with self.subTest(growth=growth):
                world = sagging_further(PhysicalWorld, growth)(floor=[dict(ARM_STAGE)], creep_beyond_out_mm=122.0, creep_deg=1.0)
                with mock.patch.object(rc, "HOLD_CHECK_S", 1.25):
                    _, run = world.run(self, writer(True), WORDS)
                self.assertEnded(run, 5)
                self.assertEqual("reach_way_back_held", run["result"]["reason_code"], run["stdout"][-1500:])
                first = fields(run, "RELEASE_ADMISSION")[0]
                self.assertEqual(("arm_reach", False), (first["policy"], first["admitted"]))
                self.assertGreaterEqual(min([up for _out, up in world.track] or [first["release_height_mm"]]), rc.PAD_THICKNESS_MM - 0.05,
                                        "the held block never under the pad's top")
                self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1], "nothing moves after the lowering")
                self.assertEqual([], gripper_opened_at(run))
                self.assertTrue(world.holding())


class TheFallbackAfterARefusedReleaseInsideTheProvenLoadedReach(mission.MissionCase):
    """A refused release inside the proven loaded reach, on a block the proven policy places (ARM_STAGE: advance_then_reach
    lowers it inside the cap, placement_policy THE LOADED REACH CAP). verify r7 replay MF2 asked that a release refused after
    a lowering there reach decision 3's in-run fallback, and the bound for a loaded lift that sags further was applied past
    the proven loaded reach only for it; the way back then stepped the lowered block back. verify r10 safety MF1 found that
    first step predicted, by the way back's own models, to press the held block onto the pad (a creep of 0.8 deg: the lift
    realizing the step with the errors it measures and the recorded growth -3.6 mm, sagging further -3.4 mm, the pad's top at
    +3), and the lead ruled on 2026-10-02 that every model is held to the pad's top at every out. So a creep of the loaded
    lift while it is held still, which leaves the hold unsteady and the release refused, ends with the lowered block held
    where it stands before any step, the cube held for the operator and the in-run fallback not reached (the first three
    tests). Decision 3's chain runs where the way back can step: a release refused at the carry height (the executor's
    planner refusing the lowering, so the block is too high), from an arm that stands where it was commanded, takes its way
    back, every step's models above the pad's top, and the same run goes on with advance_then_reach asked again (its re-plan
    with a further pulsed advance), which places it (the last test)."""

    def run_scene(self, creep_deg: float, heights=None):
        world = PhysicalWorld(floor=[dict(ARM_STAGE)], creep_beyond_out_mm=60.0, creep_deg=creep_deg)
        lowering = mock.patch.object(rc, "lower_heights_mm", lambda ctl: heights) if heights else mock.patch.dict({}, {})
        with lowering, mock.patch.object(rc, "HOLD_CHECK_S", 1.25):
            _, run = self.mission(world, writer(True), extra_env=WORDS)
        return world, run

    def assertHeldBeforeAnyStep(self, world, run, model_words: str):
        """The lowered block refused for an unsteady hold, held where it stands before any step: the sentence names the model
        that puts every step under the pad's top (model_words), nothing moves after the lowering, nothing goes on."""
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        first = fields(run, "RELEASE_ADMISSION")[0]
        self.assertEqual(("advance_then_reach", False, False), (first["policy"], first["admitted"], first["hold_steady"]))
        self.assertLessEqual(fields(run, "RELEASE_HOLD")[0]["out_after_mm"], current.ARM_LOADED_REACH_OUT_MM, "lowered inside the proven loaded reach")
        self.assertNotIn("REACH_RETRACT_STEP", run["states"], "no step of the way back was sent")
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])
        self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1], "nothing moves after the lowering")
        refused = [line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED")][-1]
        self.assertIn(model_words, refused)
        self.assertIn("under the pad's top", refused)
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding())
        self.assertLessEqual(max(target["out_mm"] for _label, target in loaded_commands(run)), current.ARM_LOADED_REACH_OUT_MM + 0.05)

    def test_a_release_refused_after_the_lowering_holds_the_lowered_block_where_it_stands(self):
        self.assertHeldBeforeAnyStep(*self.run_scene(0.8), "should the loaded lift sag further")

    def test_a_larger_creep_from_a_higher_lowering_holds_it_where_it_stands_too(self):
        self.assertHeldBeforeAnyStep(*self.run_scene(1.2, (12.0,)), "should the loaded lift sag further")

    def test_a_release_refused_at_the_carry_height_takes_its_way_back_and_goes_on_with_the_fallback(self):
        """The executor's planner refuses every lowering past +60 mm out (refusing_far_lowerings): advance_then_reach's block
        stays at the carry height and its release is refused as too high. The arm stands where it was commanded, so the way
        back, every step planned from the measured tool and every model of the loaded lift above the pad's top, brings it to
        the carry pose, and advance_then_reach asked again places it with a further pulsed advance."""
        world = refusing_far_lowerings(PhysicalWorld, 60.0)(floor=[dict(ARM_STAGE)])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        first = fields(run, "RELEASE_ADMISSION")[0]
        self.assertEqual(("advance_then_reach", False), (first["policy"], first["admitted"]))
        self.assertGreater(first["release_height_mm"], current.RELEASE_MAX_HEIGHT_MM, "refused as too high, at the carry height")
        steps = fields(run, "REACH_RETRACT_STEP")
        self.assertTrue(steps, "the way back stepped from the carry height")
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))
        for step in steps:
            for name in ("stall_predicted_up_mm", "realized_predicted_up_mm", "further_predicted_up_mm"):
                self.assertGreaterEqual(step[name], rc.PAD_THICKNESS_MM, (name, step))
        self.assertIn("REACH_RETRACT", run["states"])
        self.assertEqual([("advance_then_reach", "advance_then_reach")], [(entry["from_policy"], entry["to_policy"]) for entry in fields(run, "PLACEMENT_FALLBACK")],
                         "the fallback asks advance_then_reach again first, from where the run stands")
        self.assertEqual(("advance_then_reach", True), (fields(run, "RELEASE_ADMISSION")[-1]["policy"], fields(run, "RELEASE_ADMISSION")[-1]["admitted"]))
        self.assertEqual(1, len(gripper_opened_at(run)))
        self.assertLessEqual(max(target["out_mm"] for _label, target in loaded_commands(run)), current.ARM_LOADED_REACH_OUT_MM + 0.05)

    def test_a_creep_that_leaves_the_block_at_the_pads_top_still_holds_it_where_it_stands(self):
        """The stall bound (a loaded lift that does not move) keeps its reach at every out: a 1.2 deg creep from the release
        height leaves the block about 3 mm up, where any step would press it onto the pad. Nothing is sent."""
        world, run = self.run_scene(1.2)
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        self.assertNotIn("REACH_RETRACT_STEP", run["states"])
        refused = [line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED")][-1]
        self.assertIn("should the loaded lift not move", refused)
        self.assertTrue(world.holding())


class TheProvenPolicyGoesOnAfterAMeasuredAdvance(LongCarryCase):
    """verify r2 replay MUST-FIX 2 made decision 3's "then the proven policy" go on with the proven rule's base branch where
    the proven plan from the moved anchor is the arm alone. Row 8 of the replay (the start of cycle-run-20260920T103136Z,
    pad (+11.3, -168.1) mm, coarse +7.3), run as the runtime's respawn of its BASE_ADVANCE binding, the World's approach lead
    at 0, a loaded arm that creeps 1 deg while it is held still past +90 mm out, and the slightly drooping lowering of
    ARM_STAGE, reached it from a LOWERED refused release. Since verify r6 safety MF1 the way back sends nothing from such a
    lowered block (a lift that sags further as on record would end it under the pad's top), so both scenes now hold the
    block where it stands: the residual is named in reach_carry (THE WAY BACK UNDER LOAD, What it costs), and the in-run
    fallback runs from refusals the way back comes back from (AdvanceThenReachFallsShortThenTheProvenPolicy)."""

    ROW8 = {"pad_m": [0.0113, -0.1681], "contact_px": [553.0, 545.0 + (11.4 - 7.3) * current.LOW_POSE_PX_PER_RADIAL_MM]}

    def assertHeldAfterTheLowering(self, run, world, policy: str):
        """verify r6 safety MF1: a lowered block whose release was refused stands where a step back could take it under the
        pad's top should the loaded lift sag further as two recorded loaded raises at extension did: the way back sends
        nothing, and the arm holds the cube where it stands (the lead's decision A over decision 3's chain there)."""
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        self.assertEqual((policy, False), (fields(run, "RELEASE_ADMISSION")[-1]["policy"], fields(run, "RELEASE_ADMISSION")[-1]["admitted"]))
        self.assertEqual("PLACE lower", [label for label, _start, _target in planned_moves(run)][-1], "nothing moves after the lowering")
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding())

    def test_a_base_bound_run_whose_lowered_release_is_refused_after_the_measured_advance_holds_the_block(self):
        """It used to come back in bounded steps and be placed by the proven rule's base branch; the base branch's plan
        from the moved anchor stays pinned by test_placement_executor (TheProvenPolicyGoesOnFromAMovedAnchor)."""
        with mock.patch.object(current, "APPROACH_REACH_LEAD_MM", 0.0), mock.patch.object(rc, "HOLD_CHECK_S", 1.25):
            world = PhysicalWorld(floor=[dict(self.ROW8)], creep_beyond_out_mm=90.0, creep_deg=1.0)
            _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEqual("BASE_ADVANCE", fields(run, "TARGET_SELECTED")[0]["mode"])
        self.assertTrue(run["base_pulses"], "the measured advance was driven")
        self.assertHeldAfterTheLowering(run, world, "advance_then_reach")

    def test_a_lowering_that_droops_a_little_holds_the_block_arm_reach_refused(self):
        """DroopingWorld at 0.5 deg of shoulder_lift and 0.8 of elbow_flex: arm_reach's lowered release is refused on its
        margin, and the block stands where a step back under a lift sagging further would end it under the pad's top (it used
        to go on with advance_then_reach and then the proven policy, all three in one run)."""
        world = DroopingWorld(floor=[dict(ARM_STAGE)], lower_droop=(0.5, 0.8))
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertHeldAfterTheLowering(run, world, "arm_reach")
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])


class ABaseBoundRunWhoseMeasuredGraspLetsTheArmAlonePlaceIt(LongCarryCase):
    """SEARCH charges the worst recorded grasp under-read (22 mm) and binds a block just beyond the arm's reach to
    advance_then_reach with a small advance: the run is bound BASE_ADVANCE, and the runtime's one respawn carries the
    capability. With the approach lead at 0 the World's jaws close about 12 mm beyond the coarse estimate, the recorded mean
    under-read (the stock World closes 26 mm beyond it, more than the charge), so once held the arm alone would place the
    block. A run bound BASE_ADVANCE is placed only through the capability's approach, so the least motion it may execute is
    advance_then_reach's smallest pulsed advance."""

    SIDE = {"pad_m": [0.150, -0.166], "contact_px": list(ARM_STAGE["contact_px"])}
    CENTRE = {"pad_m": [0.0, -0.165], "contact_px": list(ARM_STAGE["contact_px"])}

    def run_as_the_runtime_runs_it(self, floor):
        with mock.patch.object(current, "APPROACH_REACH_LEAD_MM", 0.0):
            _, first = self.mission(PhysicalWorld(floor=[dict(floor)]), writer(False), extra_env=WORDS)  # the first spawn, without the capability
            world = PhysicalWorld(floor=[dict(floor)])
            _, respawn = self.mission(world, writer(True), extra_env=WORDS)  # the one respawn, carrying it
        return first, world, respawn

    def assertPlacedWithTheSmallestPulsedAdvance(self, first, world, respawn):
        self.assertEnded(first, 5)
        self.assertEqual(("mission_capability_required", []), (first["result"]["reason_code"], first["sent"]))
        selected = fields(first, "TARGET_SELECTED")[0]
        self.assertEqual(("advance_then_reach", "BASE_ADVANCE"), (selected["placement_policy"], selected["mode"]))
        self.assertEnded(respawn, 0)
        self.assertEqual("placed_new", respawn["result"]["outcome"])
        chosen = fields(respawn, "PLACEMENT_POLICY")[0]
        plans = {plan["policy"]: plan for plan in chosen["policies"]}
        self.assertEqual((True, "ARM_ONLY", 0.0), (plans["arm_reach"]["admitted"], plans["arm_reach"]["mode"], plans["arm_reach"]["base_advance_mm"]),
                         "once held, the arm alone would place it")
        self.assertEqual(("advance_then_reach", "BASE_ADVANCE"), (chosen["policy"], chosen["plan"]["mode"]))
        self.assertGreater(chosen["plan"]["base_advance_mm"], current.BASE_ADVANCE_TOLERANCE_MM)
        self.assertLessEqual(chosen["plan"]["base_advance_mm"], selected["placement_plan"]["base_advance_mm"])
        admission = fields(respawn, "RELEASE_ADMISSION")[-1]
        self.assertEqual(("advance_then_reach", True), (admission["policy"], admission["admitted"]))
        self.assertTrue(current.BASE_ADVANCE_TOLERANCE_MM < admission["base_advance_measured_mm"] < 30.0, admission["base_advance_measured_mm"])
        self.assertTrue(world.relay_pulses, "the advance went through the capability, as a BASE_ADVANCE result needs")
        self.assertEqual(1, len(gripper_opened_at(respawn)))
        self.assertNotIn("PLACEMENT_FALLBACK", respawn["states"])
        self.assertTrue(fields(respawn, "BASE_RETURNED")[0]["value"])

    def test_a_block_by_the_side_edge_that_no_word_refuses_is_placed_not_held(self):
        with mock.patch.object(current, "APPROACH_REACH_LEAD_MM", 0.0):
            _, installed = self.mission(PhysicalWorld(floor=[dict(self.SIDE)]), writer(True))
        self.assertEqual((tb.REFUSED_CANDIDATE_NOT_PLACEABLE, []), (installed["result"]["reason_code"], installed["sent"]), "with no word nothing is picked up")
        self.assertPlacedWithTheSmallestPulsedAdvance(*self.run_as_the_runtime_runs_it(self.SIDE))

    def test_a_block_on_the_centreline_is_placed_with_the_smallest_pulsed_advance_not_the_proven_whole_advance(self):
        first, world, respawn = self.run_as_the_runtime_runs_it(self.CENTRE)
        self.assertPlacedWithTheSmallestPulsedAdvance(first, world, respawn)
        proven = {plan["policy"]: plan for plan in fields(respawn, "PLACEMENT_POLICY")[0]["policies"]}["proven"]
        self.assertGreater(proven["base_advance_mm"], 60.0, "the proven stage would have driven its whole advance")


class AdvanceThenReachFromTheLiveStart(mission.MissionCase):
    def world(self):
        return PhysicalWorld(floor=[dict(LIVE)])

    def test_with_no_word_it_is_refused_as_the_robot_refused_it(self):
        _, run = self.mission(self.world(), writer(True))
        self.assertEnded(run, 5)
        self.assertEqual((tb.REFUSED_CANDIDATE_NOT_PLACEABLE, []), (run["result"]["reason_code"], run["sent"]))

    def test_under_the_words_it_is_placed_inside_the_proven_loaded_reach_with_the_advance_that_keeps_it_there(self):
        """The operator's ruling of 2026-10-02 (Q8, verify r10 replay MUST-FIX 1): a plan under the proven loaded reach fits
        the 120 mm budget, so the block is planned and carried under it: SEARCH binds advance_then_reach with about 97 mm of
        base and a carry to 58 mm out (its longer carry planned 53 mm of base and a carry to 113 mm out), the measured advance
        stays inside the budget, every loaded command stays inside +95 mm out, and the reach after the advance places it."""
        world = self.world()
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        selected = fields(run, "TARGET_SELECTED")[0]
        self.assertEqual(("advance_then_reach", "BASE_ADVANCE"), (selected["placement_policy"], selected["mode"]))
        self.assertEqual((97.0, 58.0), (selected["placement_plan"]["base_advance_mm"], selected["placement_plan"]["arm_out_mm"]))
        admission = fields(run, "RELEASE_ADMISSION")[-1]
        self.assertEqual(("advance_then_reach", True), (admission["policy"], admission["admitted"]))
        self.assertLess(admission["base_advance_measured_mm"], current.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertGreater(admission["base_advance_measured_mm"], 80.0, "the base takes the block to where the capped carry places it")
        self.assertGreater(admission["fk_travel_from_grasp_mm"], 15.0, "the arm reached after the advance")
        commands = loaded_commands(run)
        self.assertTrue(commands)
        self.assertLessEqual(max(target["out_mm"] for _label, target in commands), current.ARM_LOADED_REACH_OUT_MM + 0.05)
        replan = fields(run, "REACH_REPLAN")[0]
        self.assertTrue(replan["admitted"])
        self.assertLessEqual(replan["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM - reach_model.LOADED_SHORTFALL_MAX_MM + 0.05)
        self.assertTrue(world.relay_pulses)
        self.assertTrue(fields(run, "BASE_RETURNED")[0]["value"])
        self.assertEqual({"object_secured": True, "placed": True, "base_returned": True}, run["result"]["predicates"])
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])
        self.assertEqual(1, len(gripper_opened_at(run)))


class AdvanceThenReachFallsShortThenTheProvenPolicy(LongCarryCase):
    """A block 45 mm outside the near edge: advance_then_reach plans a short advance and a long reach, the proven rule a
    104 mm advance. The executor's planner here refuses every lowering past a given out (refusing_far_lowerings), so after
    the advance advance_then_reach's block stays at the carry height and its release is refused as too high; the arm, which
    realized its commands, is brought back in bounded steps planned from where the tool measures. The fallback asks
    advance_then_reach again first (its own re-plan with a further pulsed advance, verify r7 policy MF2), then the proven
    policy goes on from where the measured advances ended. A loaded arm that sags past +100 mm out lags its last command
    there, and the way back holds it before any step (the last test; a 1 deg creep at the hold check holds its lowered block
    past the proven loaded reach, TheProvenPolicyGoesOnAfterAMeasuredAdvance; a 4.5 deg sag holds at the way back,
    ArmReachFallsShortThenAdvanceThenReach)."""

    FAR = {"pad_m": [0.0145, -0.1925], "contact_px": list(mission.CONTACT)}

    def assertTheRunsWholeAdvanceIsInTheBudgetAndReturned(self, run):
        total = sum(entry["advance_mm"] for entry in fields(run, "BASE_MEASURED") if entry["vx_mps"] > 0)
        self.assertLessEqual(total, current.BASE_MAX_TOTAL_ADVANCE_MM + current.BASE_ADVANCE_TOLERANCE_MM)
        self.assertAlmostEqual(total, fields(run, "BASE_RETURN")[0]["remaining_mm"], delta=0.2, msg="the return drives back the whole advance of the run")
        self.assertTrue(fields(run, "BASE_RETURNED")[0]["value"])
        return total

    def test_advance_then_reach_asked_again_places_it_with_a_further_pulsed_advance_before_the_proven_policy(self):
        """Its lowering past +110 mm out refused, advance_then_reach is asked again from where the run stands: a further
        pulsed advance and a nearer reach, whose lowering the planner takes, place the block with less base motion than the
        proven policy's whole advance."""
        world = refusing_far_lowerings(PhysicalWorld, 110.0)(floor=[dict(self.FAR)])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))
        self.assertEqual([("advance_then_reach", "advance_then_reach")], [(entry["from_policy"], entry["to_policy"]) for entry in fields(run, "PLACEMENT_FALLBACK")])
        candidates = {plan["policy"]: plan for plan in fields(run, "PLACEMENT_FALLBACK")[0]["candidates"]}
        self.assertEqual({"advance_then_reach", "proven"}, set(candidates), "the advance member is asked again, then the proven policy")
        self.assertLess(fields(run, "PLACEMENT_FALLBACK")[0]["plan"]["base_advance_mm"], candidates["proven"]["base_advance_mm"])
        self.assertEqual(["advance_then_reach", "advance_then_reach"], [entry["policy"] for entry in fields(run, "RELEASE_ADMISSION")])
        total = self.assertTheRunsWholeAdvanceIsInTheBudgetAndReturned(run)
        self.assertLess(total, 104.0 - current.BASE_ADVANCE_TOLERANCE_MM, "less base than the proven rule's advance")
        self.assertEqual(1, len(gripper_opened_at(run)))

    def test_the_proven_policy_goes_on_from_where_the_measured_advance_ended_inside_the_same_budget(self):
        """The planner refuses every lowering past +90 mm out: advance_then_reach's first and second releases are both
        refused, and the proven policy goes on from where the measured advances ended, inside the same budget."""
        world = refusing_far_lowerings(PhysicalWorld, 90.0)(floor=[dict(self.FAR)])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 0)
        self.assertIn("REACH_RETRACT", run["states"], "the way back came back to the carry pose")
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))
        self.assertEqual("advance_then_reach", fields(run, "TARGET_SELECTED")[0]["placement_policy"])
        self.assertEqual([("advance_then_reach", "advance_then_reach"), ("advance_then_reach", "proven")],
                         [(entry["from_policy"], entry["to_policy"]) for entry in fields(run, "PLACEMENT_FALLBACK")])
        fallback = fields(run, "PLACEMENT_FALLBACK")[-1]
        self.assertGreater(fallback["base_advance_so_far_mm"], current.BASE_ADVANCE_TOLERANCE_MM)
        plans = fields(run, "PLACEMENT_PLAN")
        self.assertEqual(["advance_then_reach", "advance_then_reach", None], [plan.get("policy") for plan in plans], "the proven stage states its own plan, from the moved anchor")
        self.assertGreater(plans[-1]["anchor_pad_m"][1], self.FAR["pad_m"][1], "the proven plan starts where the measured advance ended")
        admission = fields(run, "RELEASE_ADMISSION")[-1]
        self.assertNotIn("policy", admission, "the proven stage released it")
        self.assertTheRunsWholeAdvanceIsInTheBudgetAndReturned(run)
        self.assertEqual("placed_new", run["result"]["outcome"])
        self.assertEqual(1, len(gripper_opened_at(run)))

    def test_the_operators_block_is_placed_by_advance_then_reachs_own_further_advance(self):
        """The operator's block: after the measured advance its lowering past +110 mm out is refused by the planner and the
        way back comes back. The proven rule cannot place the block inside what is left of the budget, and it used to end
        here held, with a sentence saying no policy of the run places it by moving the base (verify r7 policy MF2);
        advance_then_reach asked again advances a little further and places it."""
        world = refusing_far_lowerings(PhysicalWorld, 110.0)(floor=[dict(LIVE)])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 0)
        self.assertEqual("placed_new", run["result"]["outcome"])
        fallback = fields(run, "PLACEMENT_FALLBACK")[0]
        self.assertEqual(("advance_then_reach", "advance_then_reach"), (fallback["from_policy"], fallback["to_policy"]))
        proven = [plan for plan in fallback["candidates"] if plan["policy"] == "proven"][0]
        self.assertGreater(proven["base_advance_mm"], current.BASE_MAX_TOTAL_ADVANCE_MM - fallback["base_advance_so_far_mm"], "the proven rule needs more than the budget left")
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))
        self.assertLess(self.assertTheRunsWholeAdvanceIsInTheBudgetAndReturned(run), current.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertEqual(1, len(gripper_opened_at(run)))

    def test_a_block_no_policy_asked_places_from_there_stays_held_at_the_carry_pose_and_the_refusal_names_what_it_asked(self):
        """The operator's block with every lowering past +80 mm out refused: advance_then_reach's release is refused twice,
        and the proven rule cannot place the block inside what is left of the budget. The run ends with the cube held at the
        carry pose, and its refusal names the policies it asked from there and the budget left, never every policy of the run."""
        world = refusing_far_lowerings(PhysicalWorld, 80.0)(floor=[dict(LIVE)])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_policies_exhausted_while_held", run["result"]["reason_code"])
        self.assertEqual([("advance_then_reach", "advance_then_reach"), ("advance_then_reach", None)],
                         [(entry["from_policy"], entry["to_policy"]) for entry in fields(run, "PLACEMENT_FALLBACK")])
        self.assertEqual(["proven"], [plan["policy"] for plan in fields(run, "PLACEMENT_FALLBACK")[-1]["candidates"]])
        refused = [line for line in run["stdout"].splitlines() if line.startswith("CYCLE FAILED")][-1]
        self.assertIn("none of the placement policies asked from here (proven) places the block by moving the base within the", refused)
        self.assertNotIn("no placement policy of this run", refused)
        self.assertEqual([], gripper_opened_at(run))
        retract = fields(run, "REACH_RETRACT")[-1]
        grasp = fields(run, "PLACEMENT_PLAN")[0]["grasp_out_mm"]
        self.assertLessEqual(retract["out_after_mm"] - grasp, rc.RETRACT_WITHIN_MM + 0.1)
        total = sum(entry["advance_mm"] for entry in fields(run, "BASE_MEASURED") if entry["vx_mps"] > 0)
        self.assertLess(total, current.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run))

    def test_a_further_advance_whose_last_pulse_carries_the_run_past_the_budget_ends_there_with_the_cube_held(self):
        """verify r9 safety MF1: the block 60 mm farther than FAR. Its first advance is about 102 mm, the lowering is refused,
        the way back comes back and advance_then_reach is asked again for its least pulsed advance. The base slips on the
        first three pulses of that further advance and the fourth moves as the World's base moves: the run's advance passes
        120 mm on the loop's last pulse, where the loop's own check counts this advance alone. The run ends right after that
        pulse, the cube held at the low scan pose: no approach, no carry, no release, no base return."""
        block = {"pad_m": [self.FAR["pad_m"][0], self.FAR["pad_m"][1] - 0.060], "contact_px": list(self.FAR["contact_px"])}
        world = slipping_after_the_way_back(refusing_far_lowerings(PhysicalWorld, 110.0), 3)(floor=[block])
        _, run = world.run(self, writer(True), WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_further_advance_past_bound", run["result"]["reason_code"])
        self.assertEqual([("advance_then_reach", "advance_then_reach")], [(entry["from_policy"], entry["to_policy"]) for entry in fields(run, "PLACEMENT_FALLBACK")])
        self.assertTrue(assert_way_back_from_the_measured_tool(self, run), "the way back came back before the further advance")
        self.assertEqual(3, len(world.slipped))
        forward = [entry["advance_mm"] for entry in fields(run, "BASE_MEASURED") if entry["vx_mps"] > 0]
        self.assertGreater(sum(forward), current.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertLessEqual(sum(forward) - forward[-1], current.BASE_MAX_TOTAL_ADVANCE_MM, "the run passed the budget on that pulse")
        self.assertEqual("BASE_MEASURED", run["states"][-1], "no state after the pulse that passed the budget")
        self.assertEqual([], run["sent"][world.sent_at_forward_pulses[-1]:], "no command after it")
        self.assertNotIn("BASE_RETURN", run["states"])
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding())

    def test_a_loaded_arm_that_lags_after_the_measured_advance_is_held_before_any_step(self):
        """verify r7 safety MF1, on the scene this class used to place: the loaded arm sags up to 2.5 deg past +100 mm out,
        after the advance no lowering height is predicted clear of the executor's floor, and the release is refused as too
        high. The way back, planned from the last command, used to raise the lagging loaded arm toward the far carry target,
        which the World realized and no record shows; planned from the measured tool, no step is inside the records, and the
        arm holds the cube where the carry left it."""
        world = PhysicalWorld(floor=[dict(self.FAR)], loaded_reach_out_mm=100.0, loaded_sag_max_deg=2.5)
        _, run = self.mission(world, writer(True), extra_env=WORDS)
        self.assertEnded(run, 5)
        self.assertEqual("reach_way_back_held", run["result"]["reason_code"])
        self.assertEqual("advance_then_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])
        self.assertTrue(run["base_pulses"], "the measured advance was driven")
        self.assertNotIn("REACH_RETRACT_STEP", run["states"])
        self.assertNotIn("PLACEMENT_FALLBACK", run["states"])
        self.assertEqual([], gripper_opened_at(run))
        self.assertTrue(world.holding())


class MultiBlockBindsTheInstalledRulesBlockAmongBlocksTheProvenPolicyPlaces(mission.MissionCase):
    """Two blocks the proven policy places (THE BINDING TIERS' first tier), so their plans carry the loaded reach cap
    (placement_policy THE LOADED REACH CAP): ARM_STAGE, which advance_then_reach places with 48 mm of base, and a block
    nearer the pad grasped far out, which the proven policy places with the base: 55 mm 4 mm inside the near edge (NEAR), 57
    mm 2 mm inside (NEAR_OUTSIDE_THE_TIE). multi_block's least motion bound ARM_STAGE beside NEAR_OUTSIDE_THE_TIE; the lead's
    decision of 2026-10-04 binds, among blocks the proven policy places, the block the installed rule binds, the nearer one,
    and the run is the installed path's: on this World a block grasped so far out (coarse +60 mm, the World's ALIGN 26.2 mm
    past it) finds no non-contact PREGRASP pose, with or without a word, before anything moves."""

    NEAR_OUTSIDE_THE_TIE = {"pad_m": [-0.08, -0.1475 + 0.002], "contact_px": list(NEAR["contact_px"])}

    def world(self, near=None, carried=1):
        return PhysicalWorld(floor=[dict(near or self.NEAR_OUTSIDE_THE_TIE), dict(ARM_STAGE)], carried=carried)

    def test_without_multi_block_the_nearer_block_is_bound_and_needs_the_capability(self):
        for near in (NEAR, self.NEAR_OUTSIDE_THE_TIE):
            _, run = self.mission(self.world(near, 0), writer(False), extra_env=WORDS)
            with self.subTest(near=near["pad_m"]):
                self.assertEnded(run, 5)
                self.assertEqual("mission_capability_required", run["result"]["reason_code"])
                selected = fields(run, "TARGET_SELECTED")[0]
                self.assertEqual(([round(v, 4) for v in near["pad_m"]], "nearest_the_pad"), (selected["seen_pad_m"], selected["binding_rule"]))
                self.assertEqual([], run["sent"])

    def test_multi_block_binds_the_nearer_block_and_runs_it_as_the_installed_path_does(self):
        for words in ("arm_reach,advance_then_reach,multi_block", "multi_block"):
            env = {"KC_PLACEMENT_ENVELOPE": words}
            for near, advance in ((NEAR, 55.0), (self.NEAR_OUTSIDE_THE_TIE, 57.0)):
                pad = [round(v, 4) for v in near["pad_m"]]
                _, plain_first = self.mission(self.world(near, 0), writer(False))
                _, plain = self.mission(self.world(near, 0), writer(True))
                _, first = self.mission(self.world(near, 0), writer(False), extra_env=env)
                _, run = self.mission(self.world(near, 0), writer(True), extra_env=env)
                with self.subTest(words=words, near=near["pad_m"]):
                    self.assertEqual(pad, fields(plain_first, "TARGET_SELECTED")[0]["seen_pad_m"], "the installed path binds the nearer block")
                    selected = fields(first, "TARGET_SELECTED")[0]
                    self.assertEqual((pad, "nearest_the_pad", "proven", advance, "proven_admits", 0),
                                     (selected["seen_pad_m"], selected["binding_rule"], selected["placement_policy"], selected["placement_plan"]["base_advance_mm"],
                                      selected["binding_tier"], selected["tier_set_aside_candidates"]))
                    self.assertEqual(("mission_capability_required", []), (first["result"]["reason_code"], first["sent"]))
                    self.assertEqual(pad, fields(run, "TARGET_SELECTED")[0]["seen_pad_m"])
                    self.assertEqual((plain["ending"], plain["result"]["outcome"], plain["result"]["reason_code"], plain["sent"], plain["base_pulses"]),
                                     (run["ending"], run["result"]["outcome"], run["result"]["reason_code"], run["sent"], run["base_pulses"]))


class TheBindingNeverTakesABlockThePickupBandsRefuse(mission.MissionCase):
    """verify r8 replay MUST-FIX 3 (V3; the lead's decision 4: "bind the least-motion admissible block instead of refusing";
    block_selection THE PICKUP BANDS AT THE BINDING). A lies 20 mm outside the near edge by the right side edge, seen where its
    coarse pregrasp pan stands 27.8 deg from the saved low pose's pan, past SEARCH's 25 deg return bound; B lies 40 mm outside
    on the centreline. The proven rule places only B, so with no word B is bound and placed. The placement words make A
    placeable (advance_then_reach), and A lies nearer the pad and needs less base: at 055c432 both binding rules took A and
    SEARCH refused it (search_cube_beyond_return_bound), nothing picked up, although the frame held B. Under the words the
    binding sets A aside and takes B, in both listing orders; A alone is refused with the band's own code, as before. The
    binding reads the tiers before the bands (block_selection THE BINDING TIERS, the lead's decision of 2026-10-03; verify
    r13 gates MUST-FIX 1), so A, a block the proven policy refuses beside B, one it places, is set aside by its tier; the
    bands decide inside the first tier: X, A moved 12.5 mm nearer the pad, is one the proven policy places (the arm alone)
    and the return bound refuses it, and beside B the binding sets X aside and takes B (with no word 31c546c binds X, the
    nearer one, and SEARCH refuses it before anything moves)."""

    A = {"pad_m": [0.150, -0.1675], "contact_px": [880.0, 545.0 + (11.4 - 10.0) * current.LOW_POSE_PX_PER_RADIAL_MM]}
    B = {"pad_m": [0.0, -0.1875], "contact_px": [553.0, 545.0]}
    X = {"pad_m": [0.150, -0.155], "contact_px": list(A["contact_px"])}
    WORD_SETS = ("arm_reach,advance_then_reach", "arm_reach,advance_then_reach,multi_block")

    def world(self, a_first: bool, a=None):
        a = dict(a or self.A)
        return PhysicalWorld(floor=[a, dict(self.B)] if a_first else [dict(self.B), a], carried=1 if a_first else 0)

    def as_the_runtime_runs_it(self, a_first: bool, words, a=None) -> tuple:
        env = None if words is None else {"KC_PLACEMENT_ENVELOPE": words}
        _, first = self.mission(self.world(a_first, a), writer(False), extra_env=env)
        if first["result"]["reason_code"] not in runtime.SEARCH_CAPABILITY_REFUSALS:
            return first, first
        _, run = self.mission(self.world(a_first, a), writer(True), extra_env=env)
        return first, run

    def test_with_no_word_b_is_bound_and_placed(self):
        for a_first in (True, False):
            with self.subTest(a_first=a_first):
                first, run = self.as_the_runtime_runs_it(a_first, None)
                self.assertEqual([round(v, 4) for v in self.B["pad_m"]], fields(first, "TARGET_SELECTED")[0]["seen_pad_m"])
                self.assertEnded(run, 0)
                self.assertEqual("placed_new", run["result"]["outcome"])

    def test_under_the_words_the_block_the_bands_refuse_is_set_aside_and_b_is_bound_and_placed(self):
        for words in self.WORD_SETS:
            for a_first in (True, False):
                with self.subTest(words=words, a_first=a_first):
                    first, run = self.as_the_runtime_runs_it(a_first, words)
                    selected = fields(first, "TARGET_SELECTED")[0]
                    self.assertEqual(([round(v, 4) for v in self.B["pad_m"]], 0, 1), (selected["seen_pad_m"], selected["band_refused_candidates"], selected["tier_set_aside_candidates"]),
                                     "A is set aside by its tier: the proven policy places B and refuses A")
                    self.assertNotIn("search_cube_beyond_return_bound", [entry.get("reason_code") for entry in (first["result"], run["result"])])
                    self.assertEnded(run, 0)
                    self.assertEqual("placed_new", run["result"]["outcome"])
                    self.assertEqual(1, len(gripper_opened_at(run)))

    def test_inside_the_first_tier_the_block_the_bands_refuse_is_set_aside_and_b_is_bound_and_placed(self):
        x_pad, b_pad = [round(v, 4) for v in self.X["pad_m"]], [round(v, 4) for v in self.B["pad_m"]]
        for a_first in (True, False):
            first, _run = self.as_the_runtime_runs_it(a_first, None, self.X)
            with self.subTest(a_first=a_first, words=None):
                self.assertEqual((x_pad, "search_cube_beyond_return_bound", []), (fields(first, "TARGET_SELECTED")[0]["seen_pad_m"], first["result"]["reason_code"], first["sent"]),
                                 "31c546c's path binds X, the nearer block, and SEARCH refuses it before anything moves")
            for words in self.WORD_SETS:
                with self.subTest(words=words, a_first=a_first):
                    first, run = self.as_the_runtime_runs_it(a_first, words, self.X)
                    selected = fields(first, "TARGET_SELECTED")[0]
                    self.assertEqual((b_pad, 1, 0, "proven_admits"), (selected["seen_pad_m"], selected["band_refused_candidates"], selected["tier_set_aside_candidates"],
                                                                      selected["binding_tier"]))
                    self.assertEnded(run, 0)
                    self.assertEqual("placed_new", run["result"]["outcome"])
                    self.assertEqual(1, len(gripper_opened_at(run)))

    def test_a_alone_is_refused_by_the_band_the_binding_reads_as_before(self):
        for words in (None,) + self.WORD_SETS:
            env = None if words is None else {"KC_PLACEMENT_ENVELOPE": words}
            _, run = self.mission(PhysicalWorld(floor=[dict(self.A)]), writer(False), extra_env=env)
            with self.subTest(words=words):
                self.assertEqual([], run["sent"])
                wanted = "mission_binding_candidate_not_placeable" if words is None else "search_cube_beyond_return_bound"
                self.assertEqual(wanted, run["result"]["reason_code"], "the proven rule places no block at A; under the words SEARCH's band refuses it")
                if words is not None:
                    self.assertEqual(0, fields(run, "TARGET_SELECTED")[0]["band_refused_candidates"], "one placeable block is bound as before")


class TwoBlockFloorCase(mission.MissionCase):
    """A floor of two blocks off the pad, run as the runtime runs an auto target (the first spawn without the capability,
    then, when its SEARCH asks, the one respawn carrying it), the World holding B, the block the binding must take, under the
    ideal World and the recorded loaded models (physics)."""

    @staticmethod
    def physics(base) -> tuple:
        """The ideal World `base`, both recorded loaded sequences past +95 mm out and the recorded loaded lag (inside the
        reach, and graded to the first send), each on `base`."""
        return (("ideal", base), ("09:31", recorded_loaded_sequence(base, RECORDED_SEQUENCE_0931)), ("10:31", recorded_loaded_sequence(base, RECORDED_SEQUENCE_1031)),
                ("lag inside the reach", loaded_lag(base, *LAG_RECORDED_INSIDE_THE_REACH)), ("lag graded to the first send", loaded_lag(base, *LAG_GRADED_TO_THE_FIRST_SEND)))

    def spawn(self, make, floor, carried, words, carry) -> dict:
        env = None if words is None else {"KC_PLACEMENT_ENVELOPE": words}
        world = make(floor=[dict(cube) for cube in floor], carried=carried)
        if hasattr(world, "run"):
            return world.run(self, writer(carry), env)[1]
        return self.mission(world, writer(carry), extra_env=env)[1]

    def as_the_runtime_runs_it(self, make, floor, carried, words) -> tuple:
        """(the first spawn's run, the last run): the first spawn without the capability and, when its SEARCH asks for it,
        the runtime's one respawn carrying it. The World carries B, the block the binding must take."""
        first = self.spawn(make, floor, carried, words, False)
        if first["result"]["reason_code"] in runtime.SEARCH_CAPABILITY_REFUSALS:
            return first, self.spawn(make, floor, carried, words, True)
        return first, first

    def assertBIsBoundAndPlaced(self, a, b, word_sets, base=PhysicalWorld, a_tier="long_stretch", installed_places_everywhere=True) -> dict:
        """In both listing orders, under every physics: the run with no word (31c546c's path, by the default-path
        differential) binds B, and every word set's runs bind B, A read in a later tier (THE BINDING TIERS, a_tier its tier)
        and set aside, and place B wherever the run with no word places it (under every physics when
        installed_places_everywhere), no loaded command past the proven loaded reach. A alone is bound with its own plan,
        in its own tier. Returns {(listing, physics): whether the run with no word placed B}."""
        b_pad, placed_by_installed = [round(v, 4) for v in b["pad_m"]], {}
        for a_first in (True, False):
            floor, carried = ([a, b], 1) if a_first else ([b, a], 0)
            for physics, make in self.physics(base):
                first, plain = self.as_the_runtime_runs_it(make, floor, carried, None)
                installed = plain["ending"] == ("returned", 0) and plain["result"]["outcome"] == "placed_new"
                placed_by_installed[("A first" if a_first else "B first", physics)] = installed
                with self.subTest(a_first=a_first, physics=physics, words=None):
                    self.assertEqual(b_pad, fields(first, "TARGET_SELECTED")[0]["seen_pad_m"], "31c546c's path binds B")
                    if installed_places_everywhere:
                        self.assertTrue(installed, "31c546c's path places B under this physics")
                for words in word_sets:
                    first, run = self.as_the_runtime_runs_it(make, floor, carried, words)
                    with self.subTest(a_first=a_first, physics=physics, words=words):
                        selected = fields(first, "TARGET_SELECTED")[0]
                        self.assertEqual(b_pad, selected["seen_pad_m"], "B is bound")
                        self.assertEqual(("proven_admits", 1), (selected.get("binding_tier"), selected.get("tier_set_aside_candidates")),
                                         f"A, in the tier {a_tier}, is set aside")
                        if installed:
                            self.assertEnded(run, 0)
                            self.assertEqual("placed_new", run["result"]["outcome"])
                            self.assertEqual(1, len(gripper_opened_at(run)))
                        commands = loaded_commands(run)
                        if commands:
                            self.assertLessEqual(max(target["out_mm"] for _label, target in commands), current.ARM_LOADED_REACH_OUT_MM + 0.05)
        for words in word_sets:
            alone = self.spawn(base, [a], 0, words, False)
            with self.subTest(alone=words):
                selected = fields(alone, "TARGET_SELECTED")[0]
                self.assertEqual(([round(v, 4) for v in a["pad_m"]], 0, a_tier), (selected["seen_pad_m"], selected.get("tier_set_aside_candidates"), selected.get("binding_tier")),
                                 "A alone is bound with its own plan")
                if a_tier == "long_stretch":
                    self.assertGreater(selected["placement_plan"]["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM, "with its long stretch")
                else:
                    self.assertLessEqual(selected["placement_plan"]["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM - reach_model.LOADED_SHORTFALL_MAX_MM, "under the loaded reach cap")
        return placed_by_installed


class TheLongStretchIsTheLastResortAmongBlocks(TwoBlockFloorCase):
    """verify r11 safety MF1, policy MF1 and replay MUST-FIX 1, against 31c546c (block_selection THE BINDING TIERS, the long
    stretch its last tier; the binding loss accounting of 2026-10-02: a loss of a start 31c546c places, under the recorded loaded models,
    is a must-fix). Each floor holds a block B the proven policy places, which the run with no word (the installed path, as
    the default-path differential pins it) binds and places under the ideal World, both recorded loaded sequences past +95 mm
    out and the recorded loaded lag (inside the reach, and graded to the first send), beside a block A the proven policy
    refuses that only the long stretch places under the named words (no plan of the run's policies under the loaded reach
    cap fits it in the budget):
      - A2 and A3, by the right side edge as far outside the near edge as ARM_STAGE (B) and 1 mm nearer: under arm_reach
        alone only the arm alone's long stretch places them (0 mm of base, a carry to 123 to 126 mm out); the nearest-the-pad
        rule took A3, and A2 when listed first, and multi_block took either (0 mm against 70);
      - A4, on the operator's line 25 mm farther from the pad than her block, beside FAR: past the capped plans' budget its
        long advance_then_reach names 78 mm of base and a carry to 113 mm out, against FAR's plan under the cap, and
        multi_block took it;
      - verify r11 policy MF1's third face, A by the left edge (only the arm alone's long stretch, under both placement
        words) beside B, which the proven policy places with 112 mm of base: both binding rules took A;
      - its second face, on a pad seen 20 deg off its axis (TiltedPhysicalWorld): A by the left edge, whose long
        advance_then_reach names 112 mm of base and a carry to 113 mm out, lies nearer the pad than B, which the proven
        policy places with 77 mm and the words plan under the cap with 47: the nearest-the-pad rule took A.
    At 09c4973 every such run bound A, and under every recorded loaded model its long carry stopped low or regressed past
    +95 mm out and the run ended with the cube held on the stretched arm (reach_way_back_held), where 31c546c places B. Under
    the words the binding now reads A without a placement (tier_set_aside_candidates 1, B bound in the first tier) and binds
    B, in both listing orders, and every run places B, no loaded command past the proven loaded reach. A alone is bound with
    its long stretch, as the operator's ruling of 2026-10-02 leaves it to a block no plan under the cap serves."""

    PX = current.LOW_POSE_PX_PER_RADIAL_MM
    A2 = {"pad_m": [0.156, -0.158], "contact_px": list(ARM_STAGE["contact_px"])}
    A3 = {"pad_m": [0.156, -0.157], "contact_px": list(ARM_STAGE["contact_px"])}
    A4 = {"pad_m": [LIVE["pad_m"][0], -0.238], "contact_px": list(LIVE["contact_px"])}
    EDGE_A = {"pad_m": [-0.16, -0.165], "contact_px": [500.0, 545.0 + 11.4 * PX]}
    EDGE_B = {"pad_m": [-0.08, -0.20], "contact_px": [600.0, 545.0 + 11.4 * PX]}
    TURNED_A = {"pad_m": [-0.215, -0.150], "contact_px": [480.0, 545.0 + 11.4 * PX]}
    TURNED_B = {"pad_m": [0.0, -0.160], "contact_px": [600.0, 545.0 + 11.4 * PX]}

    def test_a2_by_the_side_edge_beside_arm_stage(self):
        self.assertBIsBoundAndPlaced(self.A2, ARM_STAGE, ("arm_reach", "arm_reach,multi_block"))

    def test_a3_by_the_side_edge_nearer_the_pad_than_arm_stage(self):
        self.assertBIsBoundAndPlaced(self.A3, ARM_STAGE, ("arm_reach", "arm_reach,multi_block"))

    def test_a4_past_the_capped_budget_on_the_operators_line_beside_far(self):
        self.assertBIsBoundAndPlaced(self.A4, AdvanceThenReachFallsShortThenTheProvenPolicy.FAR, ("advance_then_reach,multi_block", "arm_reach,advance_then_reach,multi_block"))

    def test_a_block_by_the_left_edge_only_the_arm_alone_places_beside_one_the_proven_policy_places(self):
        self.assertBIsBoundAndPlaced(self.EDGE_A, self.EDGE_B, ("arm_reach,advance_then_reach", "arm_reach,advance_then_reach,multi_block"))

    def test_a_block_by_the_left_edge_on_a_pad_seen_20_deg_off_its_axis_nearer_the_pad_than_one_the_proven_policy_places(self):
        self.assertBIsBoundAndPlaced(self.TURNED_A, self.TURNED_B, ("arm_reach,advance_then_reach", "advance_then_reach"), base=TiltedPhysicalWorld)

    def test_a_robust_block_under_the_cap_that_the_bands_refuse_holds_its_tier_so_the_long_stretch_beside_it_is_set_aside(self):
        """verify r13 gates MUST-FIX 1, clause 2 (the lead's decision of 2026-10-03: a block whose plan under the cap fits the
        budget robustly comes before one only the long stretch serves). The binding reads the tiers before SEARCH's pickup
        bands (block_selection.past_the_tiers_and_the_bands). TheBindingNeverTakesABlockThePickupBandsRefuse's A (27.8 deg of
        pan from the saved low pose, past the return bound; its plan under the loaded reach cap is robust) holds its tier
        beside A4, a long stretch the bands pass: A4 is set aside, A is bound, and SEARCH refuses it with the band's own code
        before anything moves. At e3756e3 the bands were read first and A4 was bound with its long stretch, which under the
        recorded loaded models ended held on the stretched arm. With no word neither block has a placement."""
        band = TheBindingNeverTakesABlockThePickupBandsRefuse.A
        for a_first in (True, False):
            floor = [dict(band), dict(self.A4)] if a_first else [dict(self.A4), dict(band)]
            for words in ("arm_reach,advance_then_reach", "arm_reach,advance_then_reach,multi_block"):
                run = self.spawn(PhysicalWorld, floor, 1 if a_first else 0, words, False)
                with self.subTest(a_first=a_first, words=words):
                    selected = fields(run, "TARGET_SELECTED")[0]
                    self.assertEqual(([round(v, 4) for v in band["pad_m"]], 0, 1, "capped_robust"),
                                     (selected["seen_pad_m"], selected["band_refused_candidates"], selected["tier_set_aside_candidates"], selected["binding_tier"]))
                    self.assertEqual(("search_cube_beyond_return_bound", []), (run["result"]["reason_code"], run["sent"]))
            plain = self.spawn(PhysicalWorld, floor, 0, None, False)
            with self.subTest(a_first=a_first, words=None):
                self.assertEqual(("mission_binding_candidate_not_placeable", []), (plain["result"]["reason_code"], plain["sent"]))

    def test_a_block_the_proven_policy_places_that_the_bands_refuse_holds_its_tier_so_the_long_stretch_beside_it_is_set_aside(self):
        """verify r13 gates MUST-FIX 1, clause 1 (the lead's decision of 2026-10-03: a block the proven policy refuses is bound
        only where no listed block is admitted by the proven policy). X (TheBindingNeverTakesABlockThePickupBandsRefuse.X) is
        one the proven policy places (the arm alone), and the return bound refuses it; A4 beside it only the long stretch
        places. 31c546c binds X and SEARCH refuses it before anything moves; under the words the binding reads X in the first
        tier, sets A4 aside, binds X and SEARCH refuses it the same way (the bands first bound A4, and under the recorded
        loaded models its long carry ended held on the stretched arm)."""
        x = TheBindingNeverTakesABlockThePickupBandsRefuse.X
        x_pad = [round(v, 4) for v in x["pad_m"]]
        for a_first in (True, False):
            floor = [dict(x), dict(self.A4)] if a_first else [dict(self.A4), dict(x)]
            plain = self.spawn(PhysicalWorld, floor, 1 if a_first else 0, None, False)
            with self.subTest(a_first=a_first, words=None):
                self.assertEqual((x_pad, "search_cube_beyond_return_bound", []), (fields(plain, "TARGET_SELECTED")[0]["seen_pad_m"], plain["result"]["reason_code"], plain["sent"]))
            for words in ("arm_reach,advance_then_reach", "arm_reach,advance_then_reach,multi_block"):
                run = self.spawn(PhysicalWorld, floor, 1 if a_first else 0, words, False)
                with self.subTest(a_first=a_first, words=words):
                    selected = fields(run, "TARGET_SELECTED")[0]
                    self.assertEqual((x_pad, 0, 1, "proven_admits"),
                                     (selected["seen_pad_m"], selected["band_refused_candidates"], selected["tier_set_aside_candidates"], selected["binding_tier"]))
                    self.assertEqual(("search_cube_beyond_return_bound", []), (run["result"]["reason_code"], run["sent"]))


class TheBlockTheProvenPolicyPlacesIsBoundFirst(TwoBlockFloorCase):
    """The lead's decision of 2026-10-03 (block_selection THE BINDING TIERS), against 31c546c: verify r12 safety MF1 and replay
    MUST-FIX 1, under the binding loss accounting of 2026-10-02. Each floor holds B, a block the proven policy places near its
    120 mm budget, seen with a far-out coarse grasp (+28 mm, so its proven plan wins its own tie at SEARCH), which the run with
    no word binds and places under the ideal World, both recorded loaded sequences past +95 mm out and the recorded loaded
    lag inside the reach (under the lag graded to the first send its release is refused), beside A, a block the proven
    policy refuses that the placement words plan under the loaded reach cap, with less base than B (V1: A seen at coarse 0,
    118 mm against B's 119; V2: A at coarse +5, 117 against 118). At 05d4cfd multi_block's least motion bound A, the World
    grasped it 26.2 mm past its coarse estimate (past SEARCH's 22 mm charge), no plan under the cap fit the budget once held,
    and the long carry ended with the cube held on the stretched arm (reach_way_back_held) under the three recorded loaded
    models, where 31c546c places B. Now every binding reads A in the tier after B's and binds B, in both listing orders,
    under every named word set, and every run places B wherever the run with no word places it, no loaded command past the
    proven loaded reach. A alone is bound with its own plan under the cap (the operator's ruling of 2026-10-02, Q8)."""

    PX = current.LOW_POSE_PX_PER_RADIAL_MM
    V1_A = {"pad_m": [0.0145, -0.234], "contact_px": [553.0, 545.0 + 11.4 * PX]}
    V1_B = {"pad_m": [-0.08, -0.2075], "contact_px": [600.0, 545.0 + (11.4 - 28.0) * PX]}
    V2_A = {"pad_m": [0.0145, -0.2305], "contact_px": [553.0, 545.0 + (11.4 - 5.0) * PX]}
    V2_B = {"pad_m": [-0.08, -0.2065], "contact_px": [600.0, 545.0 + (11.4 - 28.0) * PX]}

    def assertTheInstalledPathPlacesB(self, placed_by_installed: dict):
        for (listing, physics), placed in placed_by_installed.items():
            with self.subTest(listing=listing, physics=physics):
                self.assertEqual(physics != "lag graded to the first send", placed, "31c546c's path places B under every physics but the graded lag")

    def test_v1_a_at_coarse_0_beside_b(self):
        self.assertTheInstalledPathPlacesB(self.assertBIsBoundAndPlaced(self.V1_A, self.V1_B, ("arm_reach,advance_then_reach,multi_block", "advance_then_reach,multi_block",
                                                                                                "arm_reach,advance_then_reach"), a_tier="capped", installed_places_everywhere=False))

    def test_v2_a_at_coarse_5_beside_b(self):
        self.assertTheInstalledPathPlacesB(self.assertBIsBoundAndPlaced(self.V2_A, self.V2_B, ("arm_reach,advance_then_reach,multi_block",), a_tier="capped",
                                                                        installed_places_everywhere=False))


class TheRobustPlanUnderTheCapIsBoundFirst(TwoBlockFloorCase):
    """The lead's decision of 2026-10-03, its second clause (block_selection THE BINDING TIERS): where no listed block is one
    the proven policy places, a block whose plan under the loaded reach cap still fits the budget at the record's largest
    grasp under-read (the coarse grasp plus 30.5 mm) is bound before one whose plan fits only inside SEARCH's 22 mm charge.
    N lies 67.5 mm outside the near edge, seen at coarse +25 mm (116 mm of base under the cap at SEARCH); R lies 80 mm
    outside, seen at coarse 0 (111 mm). The proven policy refuses both, so the run with no word refuses the floor before
    anything moves. The nearest-the-pad rule took N, and the World grasped it 26.2 mm past its coarse estimate, past SEARCH's
    charge, where no plan under the cap fits the budget: the run was planned with the long stretch. Now every binding rule
    takes R, in both listing orders, and R's plan stays under the cap once held at the World's grasp. N alone is bound with
    its own plan (the operator's ruling of 2026-10-02, Q8)."""

    PX = current.LOW_POSE_PX_PER_RADIAL_MM
    N = {"pad_m": [0.0145, -0.215], "contact_px": [553.0, 545.0 + (11.4 - 25.0) * PX]}
    R = {"pad_m": [-0.06, -0.2275], "contact_px": [600.0, 545.0 + 11.4 * PX]}
    WORD_SETS = ("arm_reach,advance_then_reach", "advance_then_reach", "arm_reach,advance_then_reach,multi_block")

    def test_every_binding_rule_takes_r_and_places_it_inside_the_proven_loaded_reach(self):
        self.assertGreater(current.placed_margin_mm(self.N["pad_m"]), current.placed_margin_mm(self.R["pad_m"]), "not vacuous: N lies nearer the pad")
        r_pad = [round(v, 4) for v in self.R["pad_m"]]
        for n_first in (True, False):
            floor, carried = ([self.N, self.R], 1) if n_first else ([self.R, self.N], 0)
            for physics, make in self.physics(PhysicalWorld):
                plain = self.spawn(make, floor, carried, None, False)
                with self.subTest(n_first=n_first, physics=physics, words=None):
                    self.assertEqual(("mission_binding_candidate_not_placeable", []), (plain["result"]["reason_code"], plain["sent"]), "31c546c's path refuses the floor")
                for words in self.WORD_SETS:
                    first, run = self.as_the_runtime_runs_it(make, floor, carried, words)
                    with self.subTest(n_first=n_first, physics=physics, words=words):
                        selected = fields(first, "TARGET_SELECTED")[0]
                        self.assertEqual((r_pad, "capped_robust", 1), (selected["seen_pad_m"], selected["binding_tier"], selected["tier_set_aside_candidates"]))
                        held = fields(run, "PLACEMENT_POLICY")
                        self.assertTrue(held and held[0]["plan"]["arm_out_mm"] <= current.ARM_LOADED_REACH_OUT_MM - reach_model.LOADED_SHORTFALL_MAX_MM, "planned under the cap once held")
                        self.assertEnded(run, 0)
                        self.assertEqual("placed_new", run["result"]["outcome"])
                        self.assertEqual(1, len(gripper_opened_at(run)))
                        self.assertLessEqual(max(target["out_mm"] for _label, target in loaded_commands(run)), current.ARM_LOADED_REACH_OUT_MM + 0.05)
        for words in self.WORD_SETS:
            alone = self.spawn(PhysicalWorld, [self.N], 0, words, False)
            with self.subTest(alone=words):
                selected = fields(alone, "TARGET_SELECTED")[0]
                self.assertEqual(([round(v, 4) for v in self.N["pad_m"]], "capped", 0), (selected["seen_pad_m"], selected["binding_tier"], selected["tier_set_aside_candidates"]),
                                 "N alone is bound with its own plan")


class TheInstalledRuleFloorCase(TwoBlockFloorCase):
    """A floor of two blocks the proven policy places, A (its proven plan the arm alone at SEARCH) beside B (nearer the pad,
    its proven plan the base), as TwoBlockFloorCase runs it, against the run with no word (the lead's decision of 2026-10-04:
    among blocks the proven policy places, every binding binds the block the installed rule binds, whatever multi_block
    says, and the block is placed wherever the installed path places it)."""

    WORD_SETS = ("multi_block", "arm_reach,advance_then_reach,multi_block", "advance_then_reach,multi_block", "arm_reach,multi_block")

    def assertTheInstalledBlockIsBoundAndPlaced(self, a, b, physics, placed_by_installed, **script):
        """In both listing orders, under each of `physics`: the run with no word (31c546c's path, by the default-path
        differential) binds B and places it exactly under the physics `placed_by_installed` names, and every multi_block word
        set binds B in the first tier, nothing set aside, its record naming the installed rule (nearest_the_pad), and places
        it wherever the run with no word does. A alone: bound with its proven arm-alone plan, and run as with no word."""
        a_pad, b_pad = [round(v, 4) for v in a["pad_m"]], [round(v, 4) for v in b["pad_m"]]
        for a_first in (True, False):
            floor, carried = ([a, b], 1) if a_first else ([b, a], 0)
            for name, base in physics:
                make = (lambda base: lambda **kw: base(**kw, **script))(base)
                first, plain = self.as_the_runtime_runs_it(make, floor, carried, None)
                installed = plain["ending"] == ("returned", 0) and plain["result"]["outcome"] == "placed_new"
                with self.subTest(a_first=a_first, physics=name, words=None):
                    self.assertEqual(b_pad, fields(first, "TARGET_SELECTED")[0]["seen_pad_m"], "31c546c's path binds B")
                    self.assertEqual(name in placed_by_installed, installed, "31c546c's path places B")
                for words in self.WORD_SETS:
                    first, run = self.as_the_runtime_runs_it(make, floor, carried, words)
                    with self.subTest(a_first=a_first, physics=name, words=words):
                        selected = fields(first, "TARGET_SELECTED")[0]
                        self.assertEqual((b_pad, "nearest_the_pad", "proven_admits", 0),
                                         (selected["seen_pad_m"], selected["binding_rule"], selected["binding_tier"], selected["tier_set_aside_candidates"]))
                        if installed:
                            self.assertEnded(run, 0)
                            self.assertEqual("placed_new", run["result"]["outcome"])
                            self.assertEqual(1, len(gripper_opened_at(run)))
        plain = self.spawn(lambda **kw: PhysicalWorld(**kw, **script), [a], 0, None, False)
        for words in self.WORD_SETS:
            alone = self.spawn(lambda **kw: PhysicalWorld(**kw, **script), [a], 0, words, False)
            with self.subTest(alone=words):
                selected = fields(alone, "TARGET_SELECTED")[0]
                self.assertEqual((a_pad, "proven", "ARM_ONLY", "proven_admits", "nearest_the_pad"),
                                 (selected["seen_pad_m"], selected["placement_policy"], selected["mode"], selected["binding_tier"], selected["binding_rule"]))
                self.assertEqual((plain["ending"], plain["result"]["reason_code"], plain["sent"]), (alone["ending"], alone["result"]["reason_code"], alone["sent"]))


class TheInstalledBlockIsBoundWhereTheArmAloneIsFragile(TheInstalledRuleFloorCase):
    """verify r13 safety MF1 and policy MF1, against 31c546c, under the binding loss accounting of 2026-10-02 and the pin of the
    lead's decision of 2026-10-03 (a block the proven policy admits is bound and placed wherever 31c546c binds and places
    one). Each floor holds A, a block the proven policy places with the arm alone at SEARCH's coarse grasp (planned with no
    charge for the grasp's under-read, as the installed controller plans it) and with the base once the grasp is read the
    record's largest under-read, 30.5 mm, farther out, beside B, a block nearer the pad that the proven policy places with
    the base, which the run with no word binds and places:
      - F1 (verify r13 safety): A 10 mm outside the near edge seen at the grasp radius (the arm alone up to a grasp 8.4 mm
        past it), B 5 mm outside seen at coarse +25;
      - F4 (verify r13 safety): A 3 mm outside seen 10 mm nearer than the grasp radius (still the arm alone at the World's
        grasp, its proven carry to about +95 mm out), B 1 mm outside seen at coarse +25;
      - P (verify r13 policy, tier0b): A 14 mm outside seen at coarse +0.6 (the arm alone up to a grasp 1.7 mm past it), B
        5 mm outside seen at coarse +28, with the World's ALIGN boxes 60 px lower, so that the jaws close 21.5 mm past the
        coarse estimate, inside the record's largest first-close under-read (21.7 mm).
    At ef8679a multi_block's least motion bound A (0 mm of base against B's 60 to 64): once held, F1's and P's proven plan
    needed the base and the run, bound ARM_ONLY without the capability, ended mission_mode_changed_while_held with the cube
    at the grasp; F4's proven arm-only carry ended held where it stopped under both recorded loaded sequences
    (mission_arm_only_insufficient_while_held) and its release was refused under the lag inside the reach. Now every
    multi_block word set binds B, the block the installed rule takes, in both listing orders, and places it wherever the run
    with no word places it. A alone is bound with its proven arm-alone plan and runs as the run with no word runs it."""

    PX = current.LOW_POSE_PX_PER_RADIAL_MM
    F1_A = {"pad_m": [0.0145, -0.1575], "contact_px": [553.0, 545.0 + 11.4 * PX]}
    F1_B = {"pad_m": [-0.08, -0.1525], "contact_px": [600.0, 545.0 + (11.4 - 25.0) * PX]}
    F4_A = {"pad_m": [0.0145, -0.1505], "contact_px": [553.0, 545.0 + (11.4 + 10.0) * PX]}
    F4_B = {"pad_m": [-0.08, -0.1485], "contact_px": [600.0, 545.0 + (11.4 - 25.0) * PX]}
    P_A = {"pad_m": [0.0145, -0.1615], "contact_px": [553.0, 574.16]}
    P_B = {"pad_m": [-0.06, -0.1525], "contact_px": [600.0, 500.18]}
    BOXES_60_PX_LOWER = [[613, 256, 853, 498], [508, 234, 729, 476], [470, 234, 691, 476], [505, 247, 789, 565], [411, 252, 732, 592]]

    def test_f1_the_arm_alone_needs_the_base_a_few_mm_past_its_coarse_grasp(self):
        self.assertTheInstalledBlockIsBoundAndPlaced(self.F1_A, self.F1_B, self.physics(PhysicalWorld), ("ideal", "09:31", "10:31", "lag inside the reach"))

    def test_f4_the_arm_alone_still_planned_at_the_worlds_grasp_falls_short_under_the_recorded_loaded_arm(self):
        self.assertTheInstalledBlockIsBoundAndPlaced(self.F4_A, self.F4_B, self.physics(PhysicalWorld), ("ideal", "09:31", "10:31", "lag inside the reach"))

    def test_p_held_inside_the_records_first_close_under_read(self):
        physics = [entry for entry in self.physics(PhysicalWorld) if entry[0] in ("ideal", "09:31", "lag inside the reach")]
        self.assertTheInstalledBlockIsBoundAndPlaced(self.P_A, self.P_B, physics, ("ideal", "09:31", "lag inside the reach"), boxes=self.BOXES_60_PX_LOWER)


class TheInstalledBlockIsBoundWhereTheArmAloneIsRobust(TheInstalledRuleFloorCase):
    """verify r14 safety, policy and gates MF1, against 31c546c, under the binding loss accounting of 2026-10-02, settled by the
    lead's decision of 2026-10-04. Each floor holds A, a block the proven policy places with the arm alone that is still the
    arm alone 30.5 mm past its coarse grasp (robust to the record's grasps), beside B, a block nearer the pad that the proven
    policy places with the base, which the run with no word binds and places under the ideal World, both recorded loaded
    sequences past +95 mm out and the recorded loaded lag inside the reach:
      - AB_2 (verify r14 safety): A 9.6 mm inside the near edge seen at coarse +5.2 (60 mm of arm travel), B 11.6 mm inside
        seen at coarse +45 (48 mm of base);
      - AB_5 (verify r14 safety): A 4 mm inside seen at the grasp radius (69 mm of arm travel), B 5 mm inside seen at coarse
        +34, inside the record's held coarse grasps (54 mm of base);
      both with the World's ALIGN boxes 60 px lower (the grasp 21.5 mm past the coarse estimate);
      - PAIR (verify r14 policy): A 5 mm inside seen at the grasp radius (68 mm), B 6.5 mm inside seen at coarse +36 (53 mm),
        with the boxes 120 px lower (the grasp about 16.7 mm past the coarse estimate).
    At ca81faa every multi_block word set, multi_block alone included, bound A by its 0 mm of base, and the installed arm-only
    carry of A ended with the cube held: held where it stopped under the recorded loaded sequences
    (mission_arm_only_insufficient_while_held, AB_2 and AB_5), held lowered under the recorded lag inside the reach
    (release_not_admitted). Now every multi_block word set binds B, in both listing orders, and places it wherever the run
    with no word places it."""

    PX = current.LOW_POSE_PX_PER_RADIAL_MM
    AB_2_A = {"pad_m": [0.0145, -0.1379], "contact_px": [553.0, 545.0 + (11.4 - 5.2) * PX]}
    AB_2_B = {"pad_m": [-0.08, -0.1359], "contact_px": [600.0, 545.0 + (11.4 - 45.0) * PX]}
    AB_5_A = {"pad_m": [0.0145, -0.1435], "contact_px": [553.0, 545.0 + 11.4 * PX]}
    AB_5_B = {"pad_m": [-0.08, -0.1425], "contact_px": [600.0, 545.0 + (11.4 - 34.0) * PX]}
    PAIR_A = {"pad_m": [0.0, -0.1425], "contact_px": [553.0, 575.78]}
    PAIR_B = {"pad_m": [0.075, -0.141], "contact_px": [730.0, 478.58]}
    BOXES_120_PX_LOWER = [[613, 316, 853, 558], [508, 294, 729, 536], [470, 294, 691, 536], [505, 307, 789, 625], [411, 312, 732, 652]]
    PLACED_BY_INSTALLED = ("ideal", "09:31", "10:31", "lag inside the reach")

    def test_ab_2_b_seen_at_coarse_45(self):
        self.assertTheInstalledBlockIsBoundAndPlaced(self.AB_2_A, self.AB_2_B, self.physics(PhysicalWorld), self.PLACED_BY_INSTALLED,
                                                     boxes=TheInstalledBlockIsBoundWhereTheArmAloneIsFragile.BOXES_60_PX_LOWER)

    def test_ab_5_b_seen_at_a_coarse_grasp_the_record_holds(self):
        self.assertTheInstalledBlockIsBoundAndPlaced(self.AB_5_A, self.AB_5_B, self.physics(PhysicalWorld), self.PLACED_BY_INSTALLED,
                                                     boxes=TheInstalledBlockIsBoundWhereTheArmAloneIsFragile.BOXES_60_PX_LOWER)

    def test_pair_the_policy_lenses_floor(self):
        self.assertTheInstalledBlockIsBoundAndPlaced(self.PAIR_A, self.PAIR_B, self.physics(PhysicalWorld), self.PLACED_BY_INSTALLED, boxes=self.BOXES_120_PX_LOWER)


class MultiBlockBindsTheLeastMotionBlockAmongBlocksTheProvenPolicyRefuses(TwoBlockFloorCase):
    """multi_block's least motion where no listed block is one the proven policy places (the lead's decision of 2026-10-04: the
    tiers after the first). FAR lies 65 mm outside the near edge, seen at the grasp radius (96 mm of base under the loaded
    reach cap); NEAR lies 62.5 mm outside, seen at coarse +20 (106 mm); both robust under the cap. The run with no word
    refuses the floor before anything moves; the placement words without multi_block bind NEAR (the nearest-the-pad rule);
    with multi_block FAR is bound by the least motion, the record says so, and it is placed under the cap."""

    PX = current.LOW_POSE_PX_PER_RADIAL_MM
    FAR = {"pad_m": [0.0145, -0.2125], "contact_px": [553.0, 545.0 + 11.4 * PX]}
    NEAR = {"pad_m": [-0.08, -0.21], "contact_px": [600.0, 545.0 + (11.4 - 20.0) * PX]}

    def test_multi_block_binds_far_and_places_it_inside_the_proven_loaded_reach(self):
        far, near = [round(v, 4) for v in self.FAR["pad_m"]], [round(v, 4) for v in self.NEAR["pad_m"]]
        for far_first in (True, False):
            floor, carried = ([self.FAR, self.NEAR], 0) if far_first else ([self.NEAR, self.FAR], 1)
            plain = self.spawn(PhysicalWorld, floor, carried, None, False)
            nearest = self.spawn(PhysicalWorld, floor, 1 - carried, "arm_reach,advance_then_reach", False)
            with self.subTest(far_first=far_first, words=None):
                self.assertEqual(("mission_binding_candidate_not_placeable", []), (plain["result"]["reason_code"], plain["sent"]), "31c546c's path refuses the floor")
                self.assertEqual((near, "nearest_the_pad", "capped_robust"), tuple(fields(nearest, "TARGET_SELECTED")[0][key] for key in ("seen_pad_m", "binding_rule", "binding_tier")))
            for words in ("arm_reach,advance_then_reach,multi_block", "advance_then_reach,multi_block"):
                first, run = self.as_the_runtime_runs_it(PhysicalWorld, floor, carried, words)
                with self.subTest(far_first=far_first, words=words):
                    selected = fields(first, "TARGET_SELECTED")[0]
                    self.assertEqual((far, "least_motion", "capped_robust", 96.0), (selected["seen_pad_m"], selected["binding_rule"], selected["binding_tier"],
                                                                                 selected["placement_plan"]["base_advance_mm"]))
                    self.assertEnded(run, 0)
                    self.assertEqual("placed_new", run["result"]["outcome"])
                    self.assertLessEqual(max(target["out_mm"] for _label, target in loaded_commands(run)), current.ARM_LOADED_REACH_OUT_MM + 0.05)


class ARunWithoutAMissionTarget(mission.MissionCase):
    def test_the_worlds_base_advance_scene_is_placed_by_advance_then_reach_with_less_advance_than_the_proven_rule(self):
        plain = mission.run_world(PhysicalWorld(floor=[{"pad_m": mission.B_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.B_START), self.directory)
        self.assertEnded(plain, 0)
        worded = mission.run_world(PhysicalWorld(floor=[{"pad_m": mission.B_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.B_START), self.directory, extra_env=WORDS)
        self.assertEnded(worded, 0)
        proven_advance = fields(plain, "RELEASE_ADMISSION")[-1]["base_advance_measured_mm"]
        admission = fields(worded, "RELEASE_ADMISSION")[-1]
        self.assertEqual("advance_then_reach", admission["policy"])
        self.assertLess(admission["base_advance_measured_mm"], proven_advance)
        self.assertTrue(fields(worded, "PLACED")[0]["value"])
        self.assertTrue(fields(worded, "BASE_RETURNED")[0]["value"])


class WithNoWord(mission.MissionCase):
    def test_no_state_of_the_additive_executor_is_ever_reached(self):
        for world, carry in ((PhysicalWorld(floor=[{"pad_m": mission.A_START, "contact_px": mission.CONTACT}], cube_start_pad_m=mission.A_START, cube_final_pad_m=mission.A_FINAL), False),
                             (PhysicalWorld(floor=[{"pad_m": mission.B_START, "contact_px": mission.CONTACT}]), True)):
            with self.subTest(carry=carry):
                _, run = self.mission(world, writer(carry))
                self.assertEnded(run, 0)
                self.assertEqual(set(), {"PLACEMENT_POLICY", "REACH_CARRY", "REACH_CARRY_STOPPED", "RELEASE_HOLD", "REACH_RETRACT", "PLACEMENT_FALLBACK", "REACH_REPLAN"} & set(run["states"]))


class TheLoadedReachCap(mission.MissionCase):
    """The operator's ruling of 2026-10-01 ("cap at proven reach", placement_policy THE LOADED REACH CAP) on the starts of the
    long-carry scenes (LongCarryCase), every one a block the proven policy places: as the runtime runs an auto target, under
    every placement word set, no plan an additive policy binds or executes carries the loaded block past the proven loaded
    reach, no loaded command of the run passes it, and every start the run with no word places is placed (the others end as
    the run with no word ends). This World's loaded arm realizes its commands inside +95 mm out; under the lag the record
    shows there, TheRecordedStartsUnderTheLoadedLagInsideTheReach pins the same."""

    STARTS = (("ARM_STAGE", ARM_STAGE), ("CREEP_STAGE", TheRefusalWithoutTheCapabilitySaysWhatHappened.CREEP_STAGE),
              ("CENTRE", ABaseBoundRunWhoseMeasuredGraspLetsTheArmAlonePlaceIt.CENTRE), ("EDGE", AProvenTieBinding.EDGE), ("BORDER", AProvenTieBinding.BORDER),
              ("FAR", AdvanceThenReachFallsShortThenTheProvenPolicy.FAR), ("9ce42c3f", TheLoweringNeverGoesWhereTheCarryDidNotCommand.START_9CE42C3F),
              ("ROW8", TheLoweringNeverGoesWhereTheCarryDidNotCommand.START_ROW8))
    WORD_SETS = ("arm_reach,advance_then_reach", "arm_reach", "advance_then_reach", "arm_reach,advance_then_reach,multi_block")

    def run_as_the_runtime_runs_it(self, floor, words):
        env = None if words is None else {"KC_PLACEMENT_ENVELOPE": words}
        _, run = self.mission(PhysicalWorld(floor=[dict(floor)]), writer(False), extra_env=env)
        if run["result"]["reason_code"] in runtime.SEARCH_CAPABILITY_REFUSALS:
            _, run = self.mission(PhysicalWorld(floor=[dict(floor)]), writer(True), extra_env=env)
        return run

    def test_no_additive_plan_or_loaded_command_passes_the_cap_and_no_start_the_installed_path_places_is_lost(self):
        for start, floor in self.STARTS:
            plain = self.run_as_the_runtime_runs_it(floor, None)
            for words in self.WORD_SETS:
                with self.subTest(start=start, words=words):
                    run = self.run_as_the_runtime_runs_it(floor, words)
                    selected = fields(run, "TARGET_SELECTED")[0]
                    plans = [plan for plan in selected["placement_policies"] if plan["admitted"] and plan["policy"] != "proven"]
                    plans += [plan for entry in fields(run, "PLACEMENT_POLICY") for plan in entry["policies"] if plan["admitted"] and plan["policy"] != "proven"]
                    for plan in plans:
                        self.assertLessEqual(plan["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM + 0.05, plan)
                    commands = loaded_commands(run)
                    if commands:
                        self.assertLessEqual(max(target["out_mm"] for _label, target in commands), current.ARM_LOADED_REACH_OUT_MM + 0.05)
                    if plain["result"]["outcome"] == "placed_new":
                        self.assertEnded(run, 0)
                        self.assertEqual("placed_new", run["result"]["outcome"])
                    else:
                        self.assertEqual((plain["ending"], plain["result"]["reason_code"]), (run["ending"], run["result"]["reason_code"]))


class TheRecordedStartsUnderTheRecordedLoadedSequence(mission.MissionCase):
    """verify r6 replay MUST-FIX 1, settled by the operator's ruling of 2026-10-01 ("cap at proven reach", placement_policy
    THE LOADED REACH CAP), against 31c546c: past +95 mm out the loaded arm behaves as the two long loaded carries on record
    (recorded_loaded_sequence). The proven path (with no word, the installed controller's, as the default-path differential
    pins) never commands a loaded move past it and places these starts. They are blocks the proven policy places, so under
    every placement word set their plans and every loaded command of their runs stay inside the proven loaded reach: no
    loaded move reaches the recorded sag, and each is placed (without the cap the words bound them to long carries that
    stopped low after the first loaded step past +95 mm and ended with the cube held, reach_way_back_held). The operator's
    block, which the proven policy refuses, keeps the longer carry: an additive plan is still bound for it. This World's
    loaded arm realizes its commands inside +95 mm out; under the lag the record shows there,
    TheRecordedStartsUnderTheLoadedLagInsideTheReach pins the same."""

    STARTS = (("9ce42c3f", TheLoweringNeverGoesWhereTheCarryDidNotCommand.START_9CE42C3F), ("ARM_STAGE", ARM_STAGE),
              ("FAR", AdvanceThenReachFallsShortThenTheProvenPolicy.FAR), ("ROW8", TheLoweringNeverGoesWhereTheCarryDidNotCommand.START_ROW8))
    SEQUENCES = (("09:31", RECORDED_SEQUENCE_0931), ("10:31", RECORDED_SEQUENCE_1031))
    WORD_SETS = ("arm_reach,advance_then_reach", "arm_reach", "advance_then_reach", "arm_reach,advance_then_reach,multi_block")

    def make(self, sequence, floor):
        return recorded_loaded_sequence(PhysicalWorld, sequence)(floor=[dict(floor)])

    def run_as_the_runtime_runs_it(self, sequence, floor, words) -> tuple:
        """(the World of the last run, that run): the first spawn without the capability and, when its SEARCH asks for it,
        the runtime's one respawn carrying it."""
        env = None if words is None else {"KC_PLACEMENT_ENVELOPE": words}
        world = self.make(sequence, floor)
        _, run = world.run(self, writer(False), env)
        if run["result"]["reason_code"] in runtime.SEARCH_CAPABILITY_REFUSALS:
            world = self.make(sequence, floor)
            _, run = world.run(self, writer(True), env)
        return world, run

    def test_every_start_the_installed_path_places_is_placed_under_every_word_set_and_both_recorded_sequences(self):
        for name, sequence in self.SEQUENCES:
            for start, floor in self.STARTS:
                _world, plain = self.run_as_the_runtime_runs_it(sequence, floor, None)
                with self.subTest(sequence=name, start=start, words=None):
                    self.assertEnded(plain, 0)
                    self.assertEqual("placed_new", plain["result"]["outcome"], "31c546c's path places it under the recorded sequence")
                for words in self.WORD_SETS:
                    with self.subTest(sequence=name, start=start, words=words):
                        world, run = self.run_as_the_runtime_runs_it(sequence, floor, words)
                        self.assertEnded(run, 0)
                        self.assertEqual("placed_new", run["result"]["outcome"])
                        self.assertEqual([], world.track, "no loaded move of the run was commanded past the proven loaded reach")
                        for entry in fields(run, "REACH_CARRY"):
                            self.assertLessEqual(entry["commanded_out_mm"], current.ARM_LOADED_REACH_OUT_MM + 0.05)
                        for plan in [fields(run, "TARGET_SELECTED")[0].get("placement_plan")] + [entry.get("plan") for entry in fields(run, "PLACEMENT_POLICY")]:
                            if plan and plan["policy"] != "proven":
                                self.assertLessEqual(plan["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM + 0.05, plan)
                        self.assertEqual([], fields(run, "PLACEMENT_FALLBACK"))
                        self.assertEqual(1, len(gripper_opened_at(run)))

    def test_the_operators_block_which_the_proven_policy_refuses_is_placed_inside_the_proven_loaded_reach(self):
        """verify r10 replay MUST-FIX 1, settled by the operator's ruling of 2026-10-02 (Q8): the operator's block of
        2026-09-30 07:14Z, which the proven policy refuses, used to bind a carry past the proven loaded reach, and under the
        recorded loaded behaviour that run ended held on the stretched arm. A plan under the cap fits the budget, so it is
        planned and carried under the cap: as the runtime runs it, under every placement word set and both recorded
        sequences, it is placed with no loaded move commanded past +95 mm out."""
        for name, sequence in self.SEQUENCES:
            for words in self.WORD_SETS:
                if "advance_then_reach" not in words:
                    continue  # the arm alone places it under no cap: TheLiveBlock in test_placement_selector.py
                with self.subTest(sequence=name, words=words):
                    world, run = self.run_as_the_runtime_runs_it(sequence, LIVE, words)
                    selected = fields(run, "TARGET_SELECTED")[0]
                    plans = {plan["policy"]: plan for plan in selected["placement_policies"]}
                    self.assertFalse(plans["proven"]["admitted"], "the proven policy refuses it")
                    self.assertEqual("advance_then_reach", selected["placement_policy"])
                    self.assertLessEqual(selected["placement_plan"]["arm_out_mm"], current.ARM_LOADED_REACH_OUT_MM - reach_model.LOADED_SHORTFALL_MAX_MM + 0.05)
                    self.assertEnded(run, 0)
                    self.assertEqual("placed_new", run["result"]["outcome"])
                    self.assertEqual([], world.track, "no loaded move of the run was commanded past the proven loaded reach")
                    for entry in fields(run, "REACH_CARRY"):
                        self.assertLessEqual(entry["commanded_out_mm"], current.ARM_LOADED_REACH_OUT_MM + 0.05)
                    self.assertEqual(1, len(gripper_opened_at(run)))


class TheRecordedStartsUnderTheLoadedLagInsideTheReach(mission.MissionCase):
    """verify r8 policy MF1 and replay MUST-FIX 1, against 31c546c: the loaded arm lags its commands INSIDE the proven loaded
    reach too (loaded_lag: the descent's largest errors of the 2026-09-20 base placements on every loaded move, and those
    graded to the first send of a far carry). The installed path (with no word) places these starts. They are blocks the
    proven policy places, so under the placement words their plans count only on the out a carry whose commands stop at the
    proven loaded reach measures under the record, and their release keeps the proven rule's lagging-arm buffer (placement_
    policy THE LOADED REACH CAP and THE LAGGING ARM AT THE RELEASE): each is placed, no loaded command past the proven loaded
    reach (at 84b3d9f each ended reach_way_back_held: a carry that stopped at its command cap short of a plan counting on
    measuring +95 mm, or a lowering that pulled the block back past a plan's 0.5 to 3.5 mm of margin)."""

    STARTS = (("ARM_STAGE", ARM_STAGE), ("CENTRE", ABaseBoundRunWhoseMeasuredGraspLetsTheArmAlonePlaceIt.CENTRE), ("FAR", AdvanceThenReachFallsShortThenTheProvenPolicy.FAR),
              ("9ce42c3f", TheLoweringNeverGoesWhereTheCarryDidNotCommand.START_9CE42C3F), ("ROW8", TheLoweringNeverGoesWhereTheCarryDidNotCommand.START_ROW8))
    LAGS = (("recorded inside the reach", LAG_RECORDED_INSIDE_THE_REACH), ("graded to the first send", LAG_GRADED_TO_THE_FIRST_SEND))
    WORD_SETS = ("arm_reach,advance_then_reach", "advance_then_reach")

    def run_as_the_runtime_runs_it(self, lag, floor, words) -> dict:
        """The first spawn without the capability and, when its SEARCH asks for it, the runtime's one respawn carrying it."""
        env = None if words is None else {"KC_PLACEMENT_ENVELOPE": words}
        _, run = loaded_lag(PhysicalWorld, *lag)(floor=[dict(floor)]).run(self, writer(False), env)
        if run["result"]["reason_code"] in runtime.SEARCH_CAPABILITY_REFUSALS:
            _, run = loaded_lag(PhysicalWorld, *lag)(floor=[dict(floor)]).run(self, writer(True), env)
        return run

    def test_every_start_the_installed_path_places_is_placed_under_the_placement_words(self):
        for name, lag in self.LAGS:
            for start, floor in self.STARTS:
                plain = self.run_as_the_runtime_runs_it(lag, floor, None)
                with self.subTest(lag=name, start=start, words=None):
                    self.assertEnded(plain, 0)
                    self.assertEqual("placed_new", plain["result"]["outcome"], "31c546c's path places it under this loaded lag")
                for words in self.WORD_SETS:
                    with self.subTest(lag=name, start=start, words=words):
                        run = self.run_as_the_runtime_runs_it(lag, floor, words)
                        self.assertEnded(run, 0)
                        self.assertEqual("placed_new", run["result"]["outcome"])
                        commands = loaded_commands(run)
                        if commands:
                            self.assertLessEqual(max(target["out_mm"] for _label, target in commands), current.ARM_LOADED_REACH_OUT_MM + 0.05)
                        self.assertEqual(1, len(gripper_opened_at(run)))


    def test_the_operators_block_is_placed_under_the_same_lag(self):
        """The operator's ruling of 2026-10-02 (Q8): the operator's block of 2026-09-30, which the proven policy refuses, is
        planned under the same cap (a plan under it fits the 120 mm budget), and under the loaded lag the record shows inside
        the proven loaded reach it is placed, no loaded command past +95 mm out (its longer carry, past it, ended held)."""
        for name, lag in self.LAGS:
            for words in self.WORD_SETS:
                with self.subTest(lag=name, words=words):
                    run = self.run_as_the_runtime_runs_it(lag, LIVE, words)
                    self.assertEnded(run, 0)
                    self.assertEqual("placed_new", run["result"]["outcome"])
                    self.assertEqual("advance_then_reach", fields(run, "RELEASE_ADMISSION")[-1]["policy"])
                    self.assertLessEqual(max(target["out_mm"] for _label, target in loaded_commands(run)), current.ARM_LOADED_REACH_OUT_MM + 0.05)
                    self.assertEqual(1, len(gripper_opened_at(run)))

if __name__ == "__main__":
    unittest.main()
