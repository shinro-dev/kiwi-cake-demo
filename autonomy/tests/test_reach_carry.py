#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_reach_carry.py: the steps the additive placement policies execute with once the cube is held
# (autonomy/manipulation/reach_carry.py), each run alone on a scripted Cycle (stage_harness.HarnessCycle) whose arm is a
# small physical model: it realizes a carry target with a scripted joint error, the way the loaded arm realized its targets
# on record (a positive shoulder_lift or elbow_flex error puts the tool nearer and lower). What is pinned:
#   - THE CARRY is closed on the MEASURED out: every command leads the planned out by the shortfall the last step measured
#     (the first send's recorded 18 mm before any step), never by more than the worst recorded 24 mm, never past the
#     command cap (for a block the proven policy places, never past its loaded reach cap, carry_cap_mm), never more than one
#     bounded step past the measured out; it stops when the measured out arrives, when a
#     step measures the tool back (regression) or gains under the bound (stalled), when a joint misses its target by more
#     than the bound, when the gate refuses the next target (nothing is sent), when the next command would re-send the last
#     one (cap), when the next step is PREDICTED to measure the tool under the carry's floor (the last step's joint errors,
#     the shoulder_lift's grown by the recorded one-send growth, through the controller's FK: a lift error that grows with
#     the out, every value under the joint-error stop, never takes the held block down to the pad, and the step is not
#     sent), when a step measured the tool under the floor (the loaded arm sagging on both lift joints, the shape of the
#     record, brings the held block to the release height while it still moves out: nothing further is sent), or after the
#     bounded number of steps; every step states its commanded and measured out, its measured height and the height it was
#     predicted at before it was sent;
#   - THE SETTLE: a carry that falls short of the admission is retracted to the carry pose before anything else, and never
#     lowered; a carry that ended under its floor is never lowered further (the block already stands at the release height);
#     an arrived carry is lowered as the proven stage lowers, held still for the hold check and admitted by the
#     proven admission's own formula; the lowering is never commanded farther out than the carry's last command nor past
#     the plan's own lowering, and a lowering height predicted under the executor's contact floor (THE CARRY's prediction,
#     each lift error at least the largest loaded error on record) is never sent (a higher one is asked; none, and the
#     admission decides where the arm stands); a refused admission and a lowering the gate refuses end retracted, the cube
#     held, and a creeping hold far out ends with the crept block held where it stands; a gripper that left its holding band
#     ends the run where the arm stands (no arm move is possible then);
#   - THE WAY BACK UNDER LOAD (the lead's decision A of 2026-10-01): every step of it planned from the MEASURED tool (verify
#     r7 safety MF1), back toward the body at the height the tool measures and then up near it, inside the lead's per-step
#     bounds (shoulder_lift at most 8 deg, the other joints within the largest change recorded in one raise of the extended
#     arm), never farther out than the tool measures nor than the last command, at extension never more than 1 mm above the
#     measured tool (under every recorded loaded error; a loaded arm that lags its last command has no such step and holds
#     before any move), short of the executor's arrival abort should the loaded lift miss by its whole change, at most 3 mm
#     lower should it not move at all, and the held block above the pad's top under EVERY model of the loaded lift the
#     bounds compute, at every out (the lead's ruling of 2026-10-02 on verify r10 safety MF1): realizing the step with the
#     errors it measures and the recorded growth, not moving at all, and sagging further as two recorded loaded raises at
#     extension did (verify r6 safety MF1); so a block lowered to the proven release height is held where it stands before
#     any step, and a step is sent from a lowered block only where it stands high enough that no model takes it to the pad;
#     the measured state is read
#     between steps, and a joint error, a sag, a step back out or no progress holds the arm where it stands, the gripper
#     closed (reach_way_back_held), as does a state from which no step passes the bounds (a lift error the recorded growth
#     would take to the arrival abort, a held block at the pad's top, a gate that refuses every target); a lift that does not
#     realize a loaded raise past +95 mm out, as recorded, is held after one bounded step, never in the abort;
#   - THE RELEASE restates the proven stage's release, retreat, EXIT and PLACED and returns what RETURN rests on.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_reach_carry.py
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import reach_carry as rc  # noqa: E402
import reach_model as rm  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import stage_harness as harness  # noqa: E402
import test_run_single_cube_cycle as fixtures  # noqa: E402
from stage_policy import CycleFailed  # noqa: E402

# The held arm after the confirm lift, as the scripted World measures it (+21.0 mm out, +53.0 mm up, pitch 63.7).
THIRD = {"shoulder_pan": -13.14, "shoulder_lift": 11.748, "elbow_flex": 56.02, "wrist_flex": -4.075, "wrist_roll": -97.3, "gripper": 68.4}
GRASP_OUT = 21.1
UP = (0.0, 1.0)


class NoSleep:
    """The controller's `time` for a test: a sleep is recorded, never slept."""

    def __init__(self):
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(float(seconds))


def ctl_view(**overrides):
    return harness.controller_view(**{"time": NoSleep(), **overrides})


class Arm(harness.HarnessCycle):
    """A HarnessCycle whose arm realizes each target with a joint error: `errors(step, commanded_out)` gives the error of
    each lift joint (degrees, measured minus commanded) for the n-th REACH_CARRY step; or `outs(step, commanded_out)` the
    out the n-th step realizes, at the commanded height and the proven orientation (its joints then miss the target by
    whatever that takes). `lower_errors` is the error of the lowering; `creep` joints are added to the pose read after the
    lowering (the hold check's read), and the arm stays crept. `way_back_errors` is the error of every move of the way back
    (REACH_RETRACT), and with `stall_from_out_mm` a move of the way back that asks the shoulder_lift to RAISE from a pose
    whose tool measures past that out is not
    realized by the lift (it keeps its measured value, the other joints reach their targets): the loaded raises at
    extension on record (cycle-run-20260920T093143Z and T103136Z, +99.6 to +112.2 mm out) did not rise.
    `commanded` lists every arm move as (label, the tool state it was commanded to, the pose it stood at before it), and
    `moves` as (label, the joints it started from, its target joints, the joints it realized)."""

    def __init__(self, errors=None, outs=None, lower_errors=None, creep=None, pose=None, way_back_errors=None, stall_from_out_mm=None, **kwargs):
        super().__init__(pose=dict(pose or THIRD), **kwargs)
        self.errors, self.outs, self.lower_errors, self.creep = errors, outs, lower_errors, creep
        self.way_back_errors, self.stall_from_out_mm = way_back_errors, stall_from_out_mm
        self.carry_steps, self.label, self.commanded, self.moves = 0, "", [], []

    def run_pose(self, target, holding, floor_m, pan_allowance=0.0, label="", manipulation_step=True):
        self.label = label
        last = target[-1] if isinstance(target, (list, tuple)) else target
        self.commanded.append((label, cycle.tool_state({**self.current, **{name: float(value) for name, value in last.joints.items() if name in self.current and name != "gripper"}}),
                               cycle.tool_state(self.pose())))
        return super().run_pose(target, holding, floor_m, pan_allowance, label, manipulation_step)

    def _arrive(self, target, holding):
        last = target[-1] if isinstance(target, (list, tuple)) else target
        joints = {name: float(value) for name, value in last.joints.items() if name in self.current and name != "gripper"}
        commanded = cycle.tool_state({**self.current, **joints})["out_mm"]
        error = {}
        if self.label.startswith("REACH_CARRY") and self.outs is not None:
            realized = rm.joints_at(cycle, self.outs(self.carry_steps, commanded), cycle.tool_state({**self.current, **joints})["up_mm"])
            error = {name: realized[name] - joints[name] for name in cycle.LIFT_JOINTS}
            self.carry_steps += 1
        elif self.label.startswith("REACH_CARRY") and self.errors is not None:
            error = self.errors(self.carry_steps, commanded)
            self.carry_steps += 1
        elif self.label.startswith("PLACE lower") and self.lower_errors is not None:
            error = self.lower_errors
        elif self.label.startswith("REACH_RETRACT") and self.way_back_errors is not None:
            error = self.way_back_errors
        target, start = dict(joints), dict(self.current)
        for name, value in error.items():
            joints[name] += float(value)
        if (self.stall_from_out_mm is not None and self.label.startswith("REACH_RETRACT") and cycle.tool_state(start)["out_mm"] > self.stall_from_out_mm
                and target["shoulder_lift"] < start["shoulder_lift"]):
            joints["shoulder_lift"] = start["shoulder_lift"]  # a loaded raise at extension, not realized (the records)
        self.moves.append((self.label, start, target, dict(joints)))
        self.current = {**self.current, **joints}
        self.lowered = self.label.startswith("PLACE lower")  # the pose read after the lowering (the hold check's) is the one that creeps
        return harness.view(self.current)

    def pose(self) -> dict:
        if self.creep is not None and getattr(self, "lowered", False) and not getattr(self, "crept", False):
            self.current = {**self.current, **{name: self.current[name] + value for name, value in self.creep.items()}}  # held still, the arm crept, and stays there
            self.crept = True
        return dict(self.current)


def steady(lift=0.0, elbow=0.0):
    return lambda step, commanded: {"shoulder_lift": lift, "elbow_flex": elbow}


def SAG_PAST_95(step, commanded):
    """A loaded arm that, commanded past +95 mm out, misses on both lift joints just under the carry's joint-error stop: the
    shape of the recorded loaded re-sends (cycle-run-20260920T093143Z: shoulder_lift 3.54, elbow_flex 2.67 deg), which put the
    tool within a few mm of the pad's top."""
    return {"shoulder_lift": 3.9, "elbow_flex": 3.9} if commanded > 95.0 else {}


def GRADED_SAG(step, commanded):
    """A loaded arm whose shoulder_lift error grows with the commanded out past +100 mm, every value under the carry's
    joint-error stop: 1.41 deg at +100 (the first send of cycle-run-20260920T103136Z), 0.06 deg more per mm (1.69 at about
    +105, 3.5 at about +135), and the elbow_flex error of the re-send of cycle-run-20260920T093143Z (2.67 deg). The same
    errors put the tool lower the farther out it is commanded, and the lift's grows as it goes: the step that crosses the
    floor starts well above it (the graded sag of verify r2 safety MF1)."""
    if commanded <= 100.0:
        return {}
    return {"shoulder_lift": min(1.41 + 0.06 * (commanded - 100.0), 3.99), "elbow_flex": 2.67}


def recorded_growth():
    """The loaded errors of cycle-run-20260920T093143Z in the order they were measured: the first step past +100 mm
    commanded misses by the first send's 1.69 and 2.85 deg, every later one by the re-send's 3.54 and 2.67."""
    loaded = []

    def errors(step, commanded):
        if commanded <= 100.0:
            return {}
        loaded.append(step)
        return {"shoulder_lift": 1.69, "elbow_flex": 2.85} if len(loaded) == 1 else {"shoulder_lift": 3.54, "elbow_flex": 2.67}
    return errors


def states(ctx, name):
    return [entry for entry in ctx.transitions if entry["state"] == name]


class TheCarry(unittest.TestCase):
    def test_an_arm_that_realizes_every_command_arrives_in_bounded_steps(self):
        ctx, ctl = Arm(), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        self.assertLessEqual(abs(carried.out_mm - 114.0), rc.ARRIVAL_TOLERANCE_MM + 18.0)
        self.assertGreaterEqual(carried.out_mm, 114.0 - rc.ARRIVAL_TOLERANCE_MM)
        steps = states(ctx, "REACH_CARRY")
        self.assertEqual(len(carried.steps), len(steps))
        self.assertTrue(1 < len(steps) <= rc.MAX_STEPS)
        previous = 21.0
        for record in steps:
            self.assertLessEqual(record["commanded_out_mm"], previous + rc.STEP_MAX_MM + 0.1)
            self.assertLessEqual(record["commanded_out_mm"], rm.command_cap_mm(ctl) + 1e-9)
            self.assertEqual("arm_reach", record["policy"])
            previous = record["measured_out_mm"]
        self.assertEqual([("run_pose", f"REACH_CARRY arm_reach step {index}") for index in range(len(steps))], ctx.calls)
        stopped = states(ctx, "REACH_CARRY_STOPPED")
        self.assertEqual([rc.ARRIVED], [entry["reason"] for entry in stopped])
        verdict = ctx.events.predicates("reach_carry")
        self.assertEqual([True], [entry["value"] for entry in verdict])

    def test_the_first_command_leads_by_the_first_sends_shortfall_and_later_by_the_last_measured_one(self):
        ctx, ctl = Arm(errors=steady(lift=1.7, elbow=2.4)), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 50.0)
        steps = states(ctx, "REACH_CARRY")
        self.assertAlmostEqual(50.0 + rm.LOADED_SHORTFALL_FIRST_MM, steps[0]["commanded_out_mm"], delta=0.05)
        shortfall = steps[0]["commanded_out_mm"] - steps[0]["measured_out_mm"]
        self.assertGreater(shortfall, 10.0, "the scripted errors make the arm realize short")
        if len(steps) > 1:
            self.assertAlmostEqual(min(50.0 + shortfall, 50.0 + rm.LOADED_SHORTFALL_MAX_MM), steps[1]["commanded_out_mm"], delta=0.1)
        self.assertEqual(rc.ARRIVED, carried.stop)
        self.assertGreaterEqual(carried.out_mm, 50.0 - rc.ARRIVAL_TOLERANCE_MM)

    def test_a_named_cap_bounds_every_command_and_ends_the_carry_there(self):
        """THE LOADED REACH CAP of a block the proven policy places (placement_policy; carry_cap_mm): no command passes it, an
        arm that realizes its commands arrives at it, and one that lags them ends at the cap, never re-sending its last
        command past it."""
        for errors, stop in ((None, rc.ARRIVED), (steady(lift=1.0, elbow=1.0), rc.CAP)):
            ctx, ctl = Arm(errors=errors), ctl_view()
            carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 92.0, cycle.ARM_LOADED_REACH_OUT_MM)
            commands = [record["commanded_out_mm"] for record in states(ctx, "REACH_CARRY")]
            with self.subTest(errors=errors):
                self.assertTrue(commands)
                self.assertLessEqual(max(commands), cycle.ARM_LOADED_REACH_OUT_MM + 0.05)
                self.assertEqual(len(commands), len(set(commands)), "no target is sent twice")
                self.assertEqual(stop, carried.stop)
                self.assertLessEqual(carried.out_mm, cycle.ARM_LOADED_REACH_OUT_MM + 0.05)

    def test_the_carrys_cap_is_the_loaded_reach_cap_of_a_block_the_proven_policy_places_and_none_otherwise(self):
        ctl = ctl_view()

        def held(proven_admits):
            return rc.Held(view={}, grasp_out_mm=GRASP_OUT, grasp_pan_deg=0.0, anchor=(0.0145, -0.158), direction=UP, axis=UP, advanced_mm=0.0,
                           rate=cycle.BASE_RATE_PRIOR_MM_S, out_cap_mm=None, seen=None, proven_admits=proven_admits)
        self.assertEqual(cycle.ARM_LOADED_REACH_OUT_MM, rc.carry_cap_mm(ctl, held(True)))
        for refused in (False, None):
            self.assertIsNone(rc.carry_cap_mm(ctl, held(refused)), "the reach model's command cap, which leads its measured-out cap")
        with mock.patch.object(rc.placement_policy, "loaded_reach_cap_mm", lambda ctl, situation, proven_admits: rm.measured_out_cap_mm(ctl)):
            self.assertIsNone(rc.carry_cap_mm(ctl, held(True)), "a cap at the reach model's own is no cap on the commands")
        self.assertEqual(True, held(True).situation(ctl).proven_admits, "every re-plan of the run reads its cap with it")

    def test_a_block_the_proven_policy_refuses_carries_the_cap_whenever_a_plan_of_the_run_under_it_fits_the_budget(self):
        """The operator's ruling of 2026-10-02 (Q8, placement_policy THE LOADED REACH CAP): in a run that names the words, a
        held block the proven policy refuses is carried under the proven loaded reach whenever one of the run's policies
        places it there within what is left of the budget, and with the reach model's command cap otherwise; the carry reads
        the cap for the situation its plan was planned for (advance_then_reach's REACH_REPLAN)."""
        ctl = ctl_view(PLACEMENT_ENVELOPE=("arm_reach", "advance_then_reach"))

        def held(advanced_mm=0.0):
            return rc.Held(view={}, grasp_out_mm=GRASP_OUT, grasp_pan_deg=0.0, anchor=(0.0145, -0.158), direction=UP, axis=UP, advanced_mm=advanced_mm,
                           rate=cycle.BASE_RATE_PRIOR_MM_S, out_cap_mm=None, seen=None, proven_admits=False)
        self.assertEqual(cycle.ARM_LOADED_REACH_OUT_MM, rc.carry_cap_mm(ctl, held()), "a plan under the cap fits the budget")
        spent = held(cycle.BASE_MAX_TOTAL_ADVANCE_MM)
        self.assertIsNone(rc.carry_cap_mm(ctl, spent), "with no advance left the arm alone under the cap does not place it: the reach model's command cap")
        self.assertIsNone(rc.carry_cap_mm(ctl_view(), held()), "a run that names no additive policy has no plan under the cap")
        replanned = rc.placement_policy.Situation(spent.anchor, UP, GRASP_OUT, True, 0.0, None, 0.0, proven_admits=False)
        self.assertIsNone(rc.carry_cap_mm(ctl, held(), replanned), "the situation named is the one read, not the held state's")

    def test_a_shortfall_past_the_worst_recorded_ends_at_the_cap_and_is_never_re_sent(self):
        """An arm that realizes every command 30 mm short at the commanded height: the lead reaches the worst recorded
        shortfall and the next command would re-send the last one."""
        with mock.patch.object(rc, "JOINT_ERROR_MAX_DEG", 90.0):
            ctx, ctl = Arm(outs=lambda step, commanded: commanded - 30.0), ctl_view()
            carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 60.0)
        self.assertEqual(rc.CAP, carried.stop)
        commands = [record["commanded_out_mm"] for record in states(ctx, "REACH_CARRY")]
        self.assertEqual(len(commands), len(set(commands)), "no target is sent twice")
        self.assertAlmostEqual(60.0 + rm.LOADED_SHORTFALL_MAX_MM, commands[-1], delta=0.05)
        self.assertLess(carried.out_mm, 60.0 - rc.ARRIVAL_TOLERANCE_MM)

    def test_an_arm_that_sags_by_the_recorded_re_send_errors_from_its_first_step_sends_no_step_predicted_at_the_pad(self):
        """Missing by 3.5 and 3.9 deg from the first step, the loaded arm measures the tool at about +9 mm: the next step,
        commanded farther out with the lead, is predicted under the floor (about +2 mm, at the pad's top) and never sent."""
        ctx, ctl = Arm(errors=steady(lift=3.5, elbow=3.9)), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 60.0)
        self.assertEqual((rc.LOW, 1, 1), (carried.stop, len(carried.steps), len(ctx.calls)))
        self.assertGreaterEqual(carried.steps[0]["up_mm"], rc.floor_mm(ctl))
        self.assertLess(states(ctx, "REACH_CARRY_STOPPED")[-1]["predicted_up_mm"], rc.floor_mm(ctl))

    def test_a_step_that_measures_the_tool_back_ends_the_carry_there(self):
        with mock.patch.object(rc, "JOINT_ERROR_MAX_DEG", 90.0):
            ctx = Arm(outs=lambda step, commanded: commanded if step == 0 else 70.0)
            carried = rc.carry(ctx, ctl_view(), "arm_reach", harness.view(THIRD), GRASP_OUT, 100.0)
        self.assertEqual(rc.REGRESSION, carried.stop)
        self.assertEqual(2, len(carried.steps))
        self.assertAlmostEqual(-6.0, carried.steps[1]["gain_mm"], delta=0.2)
        self.assertEqual(2, len(ctx.calls), "nothing is sent after the step that went back")

    def test_a_step_that_gains_almost_nothing_ends_the_carry_there(self):
        with mock.patch.object(rc, "JOINT_ERROR_MAX_DEG", 90.0):
            ctx = Arm(outs=lambda step, commanded: commanded if step == 0 else 77.0)
            carried = rc.carry(ctx, ctl_view(), "arm_reach", harness.view(THIRD), GRASP_OUT, 100.0)
        self.assertEqual(rc.STALLED, carried.stop)
        self.assertLess(carried.steps[-1]["gain_mm"], rc.MIN_GAIN_MM)
        self.assertGreaterEqual(carried.steps[-1]["gain_mm"], 0.0)

    def test_a_joint_error_past_the_bound_ends_the_carry(self):
        ctx, ctl = Arm(errors=steady(lift=4.5)), ctl_view()
        carried = rc.carry(ctx, ctl, "advance_then_reach", harness.view(THIRD), GRASP_OUT, 100.0)
        self.assertEqual(rc.JOINT_ERROR, carried.stop)
        self.assertEqual(1, len(carried.steps))
        self.assertAlmostEqual(4.5, carried.steps[0]["joint_error_deg"], delta=0.01)

    def test_a_target_the_gate_refuses_is_never_sent(self):
        def refuse(*_args, **_kwargs):
            raise cycle.ScanRefused("scripted: the gate refuses this carry target")
        ctx = Arm()
        carried = rc.carry(ctx, ctl_view(proven_orientation_pose=refuse), "arm_reach", harness.view(THIRD), GRASP_OUT, 100.0)
        self.assertEqual((rc.GATE_REFUSAL, []), (carried.stop, ctx.calls))
        self.assertIn("gate refuses", carried.detail)
        self.assertEqual(21.0, round(carried.out_mm, 1))

    def test_a_step_the_executors_planner_refuses_ends_the_carry_where_the_arm_stands_and_nothing_is_sent(self):
        """The executor's own planner (run_pose's plan, before any motion) may refuse a target the gate accepted, as its sag
        margin refused a far lowering: the carry stops there as a gate refusal, it never escapes the policy."""
        refusal = cycle.ScanRefused("scripted: scan pose refused by the planner")
        ctx, ctl = Arm(script={"REACH_CARRY arm_reach step 1": refusal}), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        self.assertEqual(rc.GATE_REFUSAL, carried.stop)
        self.assertIn("planner", carried.detail)
        self.assertEqual(1, len(carried.steps))
        self.assertAlmostEqual(carried.steps[0]["measured_out_mm"], carried.out_mm, delta=0.05)

    def test_a_target_past_the_gates_reach_is_capped_and_never_re_sent(self):
        ctx, ctl = Arm(), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 200.0)
        self.assertEqual(rc.CAP, carried.stop)
        self.assertAlmostEqual(rm.command_cap_mm(ctl), max(record["commanded_out_mm"] for record in carried.steps), delta=0.05)
        commands = [record["commanded_out_mm"] for record in carried.steps]
        self.assertEqual(len(commands), len(set(commands)))

    def test_the_steps_are_bounded(self):
        with mock.patch.object(rc, "JOINT_ERROR_MAX_DEG", 90.0):
            ctx = Arm(outs=lambda step, commanded: 76.0 + 2.5 * step)  # every step gains 2.5 mm, and the carry never arrives
            carried = rc.carry(ctx, ctl_view(), "arm_reach", harness.view(THIRD), GRASP_OUT, 140.0)
        self.assertEqual((rc.STEPS, rc.MAX_STEPS), (carried.stop, len(carried.steps)))
        commands = [record["commanded_out_mm"] for record in carried.steps]
        self.assertEqual(sorted(set(commands)), commands, "every command is new and farther out")


    def test_a_loaded_arm_that_sags_to_the_release_height_ends_the_carry_there_and_nothing_further_is_sent(self):
        ctx, ctl = Arm(errors=SAG_PAST_95), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        self.assertEqual(rc.LOW, carried.stop)
        heights = [record["up_mm"] for record in carried.steps]
        self.assertLess(heights[-1], cycle.PLACE_HEIGHT_MM, "the last step measured the tool under the floor")
        self.assertTrue(all(height >= cycle.PLACE_HEIGHT_MM for height in heights[:-1]), heights)
        self.assertEqual(len(carried.steps), len(ctx.calls), "nothing is sent after the step that measured the tool under the floor")
        self.assertLess(max(record["joint_error_deg"] for record in carried.steps), rc.JOINT_ERROR_MAX_DEG, "the joint-error stop alone would not have ended it")
        self.assertLess(carried.out_mm, 114.0 - rc.ARRIVAL_TOLERANCE_MM)
        stopped = states(ctx, "REACH_CARRY_STOPPED")[-1]
        self.assertEqual(rc.LOW, stopped["reason"])
        self.assertAlmostEqual(heights[-1], stopped["measured_up_mm"], delta=0.05)
        self.assertEqual(cycle.PLACE_HEIGHT_MM, stopped["floor_mm"])
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("reach_carry")])

    def test_a_lift_error_that_grows_with_the_out_is_predicted_and_never_carries_the_block_under_the_floor(self):
        """The step whose measured height would cross the floor starts above it: it is predicted before it is sent (the last
        step's joint errors, the shoulder_lift's grown by the recorded one-send growth, through the controller's FK) and it
        is not sent; every step that was sent measured the tool at or above the floor."""
        for wanted in (110.0, 126.0):
            with self.subTest(wanted=wanted):
                ctx, ctl = Arm(errors=GRADED_SAG), ctl_view()
                carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, wanted)
                heights = [record["up_mm"] for record in carried.steps]
                self.assertTrue(all(height >= rc.floor_mm(ctl) for height in heights), heights)
                self.assertEqual(rc.LOW, carried.stop)
                self.assertEqual(len(carried.steps), len(ctx.calls), "the step predicted under the floor is never sent")
                self.assertLess(max(record["joint_error_deg"] for record in carried.steps), rc.JOINT_ERROR_MAX_DEG, "the joint-error stop alone would not have ended it")
                stopped = states(ctx, "REACH_CARRY_STOPPED")[-1]
                self.assertEqual(rc.LOW, stopped["reason"])
                self.assertLess(stopped["predicted_up_mm"], rc.floor_mm(ctl))
                self.assertGreaterEqual(stopped["measured_up_mm"], rc.floor_mm(ctl))
                self.assertIn("predicted", stopped["detail"])
                self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("reach_carry")])

    def test_the_recorded_one_send_growth_is_predicted_before_the_step_that_would_take_the_block_to_the_pad(self):
        """cycle-run-20260920T093143Z: 1.69 and 2.85 deg put the tool at about +14 mm, and the next send's 3.54 and 2.67 at
        about +6: the carry stops where the first loaded step measured it and sends nothing lower."""
        ctx, ctl = Arm(errors=recorded_growth()), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 126.0)
        self.assertEqual(rc.LOW, carried.stop)
        self.assertEqual(2, len(carried.steps), "the step after the first loaded one is predicted under the floor")
        self.assertAlmostEqual(1.69, carried.steps[-1]["shoulder_lift_error_deg"], delta=0.01)
        self.assertGreaterEqual(carried.steps[-1]["up_mm"], rc.floor_mm(ctl))
        self.assertEqual(len(carried.steps), len(ctx.calls))

    def test_every_step_states_the_height_it_was_predicted_at_before_it_was_sent(self):
        ctx, ctl = Arm(errors=steady(lift=1.7, elbow=2.4)), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 90.0)
        self.assertTrue(carried.steps)
        for record in carried.steps:
            self.assertIn("predicted_up_mm", record)
            self.assertGreaterEqual(record["predicted_up_mm"], rc.floor_mm(ctl))
        self.assertIsNone(states(ctx, "REACH_CARRY_STOPPED")[-1]["predicted_up_mm"], "a carry that arrived predicted nothing it did not send")

    def test_the_prediction_is_the_controllers_fk_of_the_next_target_missed_by_the_last_errors_and_the_recorded_growth(self):
        ctl = ctl_view()
        pose = cycle.proven_orientation_pose(THIRD, 0.060, cycle.CARRY_HEIGHT_MM / 1000.0)[0]
        errors = {"shoulder_lift": 1.69, "elbow_flex": 2.85, "wrist_flex": -0.15}
        missed = {**pose.joints, "shoulder_lift": pose.joints["shoulder_lift"] + 1.69 + rc.LIFT_GROWTH_DEG,
                  "elbow_flex": pose.joints["elbow_flex"] + 2.85, "wrist_flex": pose.joints["wrist_flex"] - 0.15}
        self.assertAlmostEqual(cycle.tool_state(missed)["up_mm"], rc.predicted_up_mm(ctl, pose, errors), delta=1e-9)
        self.assertAlmostEqual(cycle.tool_state({**pose.joints, "shoulder_lift": pose.joints["shoulder_lift"] + rc.LIFT_GROWTH_DEG})["up_mm"],
                               rc.predicted_up_mm(ctl, pose, {}), delta=1e-9, msg="before any step only the growth is predicted")
        self.assertEqual(1.85, rc.LIFT_GROWTH_DEG, "the largest growth one send showed on record (1.69 to 3.54 deg)")

    def test_a_carry_that_starts_under_its_floor_sends_nothing(self):
        low = harness.view({**THIRD, "shoulder_lift": THIRD["shoulder_lift"] + 25.0})
        self.assertLess(low["tool"]["up_mm"], cycle.PLACE_HEIGHT_MM)
        ctx = Arm(pose={**THIRD, "shoulder_lift": THIRD["shoulder_lift"] + 25.0})
        carried = rc.carry(ctx, ctl_view(), "arm_reach", low, GRASP_OUT, 200.0)
        self.assertEqual((rc.LOW, [], ()), (carried.stop, ctx.calls, carried.steps))


class TheSettle(unittest.TestCase):
    """The anchor (0.0145, -0.158) lies 10.5 mm outside the near edge: the admission needs 87.1 mm of FK travel."""

    ANCHOR = (0.0145, -0.158)

    def setUp(self):
        fixtures.use_synthetic_runtime(self)

    def _carried(self, ctx, ctl, wanted):
        return rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, wanted)

    def test_an_arrived_carry_is_lowered_held_still_and_admitted(self):
        ctx, ctl = Arm(), ctl_view()
        settled = rc.settle(ctx, ctl, "arm_reach", self._carried(ctx, ctl, 114.0), GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        self.assertTrue(settled.admitted)
        labels = ctx.labels("run_pose")
        self.assertEqual("PLACE lower", labels[-1])
        self.assertEqual([rc.HOLD_CHECK_S], ctl.time.slept)
        order = [ctx.states().index(name) for name in ("REACH_CARRY_STOPPED", "RELEASE_HOLD", "RELEASE_ADMISSION")]
        self.assertEqual(sorted(order), order)
        admission = states(ctx, "RELEASE_ADMISSION")[-1]
        self.assertTrue(admission["admitted"])
        self.assertEqual("arm_reach", admission["policy"])
        travel = settled.view["tool"]["out_mm"] - GRASP_OUT
        self.assertAlmostEqual(cycle.release_margin_worst_mm(self.ANCHOR, UP, travel), settled.worst_mm, delta=1e-9)
        self.assertEqual([True, True, True, True], [ctx.events.predicates(name)[-1]["value"] for name in ("release_hold", "release_margin", "release_height", "release_admitted")])
        self.assertNotIn("REACH_RETRACT", ctx.states())

    def test_a_carry_short_of_the_admission_is_retracted_and_never_lowered(self):
        """FAR needs 155 mm of FK travel: the carry to 114 mm arrives and the arm-only check refuses it at the carry height.
        The arm comes back to the carry pose (THE WAY BACK UNDER LOAD), never lowered, nothing released."""
        ctx, ctl = Arm(), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, TheWayBackUnderLoad.FAR, UP, 0.0, None)
        self.assertFalse(settled.admitted)
        self.assertEqual(rc.CARRY_SHORT, settled.refusal)
        self.assertIn("ARM_ONLY_INSUFFICIENT", ctx.states())
        self.assertNotIn("PLACE lower", ctx.labels("run_pose"))
        self.assertNotIn("RELEASE_ADMISSION", ctx.states())
        self.assertLessEqual(settled.view["tool"]["out_mm"] - GRASP_OUT, rc.RETRACT_WITHIN_MM)
        self.assertTrue(ctx.labels("run_pose")[-1].startswith("REACH_RETRACT arm_reach"))
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("arm_only_sufficient")])

    def test_a_lowering_the_gate_refuses_is_not_released_and_is_retracted(self):
        ctx, ctl = Arm(script={"PLACE lower": cycle.ScanRefused("scripted: the gate refuses the lowering")}), ctl_view()
        settled = rc.settle(ctx, ctl, "arm_reach", self._carried(ctx, ctl, 114.0), GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        self.assertFalse(settled.admitted)
        self.assertIn("PLACE_LOWER_REFUSED", ctx.states())
        self.assertFalse(states(ctx, "RELEASE_ADMISSION")[-1]["admitted"], "the cube is still at the carry height")
        self.assertLessEqual(settled.view["tool"]["out_mm"] - GRASP_OUT, rc.RETRACT_WITHIN_MM)

    def test_a_lowering_the_planner_refuses_at_the_proven_height_is_asked_again_a_little_higher(self):
        refusal = cycle.ScanRefused("scripted: no sag margin at this height")
        ctx, ctl = Arm(script={"PLACE lower": [refusal, lambda ctx: ctx._arrive(ctx.lowering, True)]}), ctl_view()
        original = ctl.proven_orientation_pose

        def remember(measured, forward_m, dz_m):
            pose = original(measured, forward_m, dz_m)
            ctx.lowering = pose[0]
            return pose
        ctl = ctl_view(proven_orientation_pose=remember)
        # 90 mm out: the proven height is predicted clear of the executor's floor even with the largest loaded errors on
        # record, so it is the executor's planner that refuses it (farther out the prediction refuses it first)
        settled = rc.settle(ctx, ctl, "arm_reach", self._carried(ctx, ctl, 90.0), GRASP_OUT, self.INSIDE, UP, 0.0, None)
        self.assertTrue(settled.admitted)
        self.assertEqual(rc.lower_heights_mm(ctl)[:2], (cycle.PLACE_HEIGHT_MM, cycle.PLACE_HEIGHT_MM + rc.LOWER_RAISE_STEP_MM))
        hold = states(ctx, "RELEASE_HOLD")[-1]
        self.assertEqual((cycle.PLACE_HEIGHT_MM + rc.LOWER_RAISE_STEP_MM, [cycle.PLACE_HEIGHT_MM]), (hold["lower_height_mm"], hold["lower_heights_refused_mm"]))
        self.assertAlmostEqual(cycle.PLACE_HEIGHT_MM + rc.LOWER_RAISE_STEP_MM, settled.view["tool"]["up_mm"], delta=0.2)
        self.assertNotIn("PLACE_LOWER_REFUSED", ctx.states())
        self.assertLessEqual(max(rc.lower_heights_mm(ctl)), cycle.RELEASE_MAX_HEIGHT_MM - rc.LOWER_HEIGHT_MARGIN_MM)

    def test_a_hold_that_creeps_is_not_released_and_the_crept_block_is_never_moved(self):
        """Lowered 12 mm up, the creep makes the hold unsteady: nothing is released. The crept block stands where a step back
        under a loaded lift that does not move, or sags further as two recorded loaded raises at extension did, would end it
        under the pad's top (verify r6 safety MF1): no step is sent, and the arm holds the cube where it stands (the lead's
        decision A)."""
        ctx, ctl = Arm(creep={"shoulder_lift": 1.5}), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)
        with mock.patch.object(rc, "lower_heights_mm", lambda ctl: (12.0,)), self.assertRaises(CycleFailed):
            rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        hold = states(ctx, "RELEASE_HOLD")[-1]
        self.assertFalse(hold["steady"])
        self.assertGreater(abs(hold["drift_mm"]), rc.HOLD_DRIFT_MAX_MM)
        self.assertEqual([False], [entry["value"] for entry in ctx.events.predicates("release_hold")])
        code, sentence, values = TheWayBackUnderLoad.failure(self, ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_NO_STEP), (code, values["held_by"]))
        self.assertIn("the pad's top", sentence)
        self.assertEqual("PLACE lower", ctx.labels("run_pose")[-1], "nothing moves after the lowering")

    def test_a_gripper_that_left_its_holding_band_ends_the_run_where_the_arm_stands(self):
        ctx, ctl = Arm(creep={"gripper": 9.0}), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)
        with self.assertRaises(CycleFailed):
            rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        self.assertEqual("reach_release_hold_lost", harness.failure_code(ctx, CycleFailed("x")))
        self.assertEqual("PLACE lower", ctx.labels("run_pose")[-1], "nothing moves after the hold check")

    def test_a_retract_the_gate_refuses_ends_the_run_where_the_arm_stands(self):
        """Every target of the way back refused by the gate: no step stays inside the bounds, the arm holds where it stands
        and the refusal names the gate's answer for the smallest step."""
        ctx, ctl = Arm(), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)

        def refuse_back(measured, forward_m, dz_m):
            raise cycle.ScanRefused("scripted: the gate refuses the retract")
        with self.assertRaises(CycleFailed) as held:
            rc.settle(ctx, ctl_view(proven_orientation_pose=refuse_back), "arm_reach", carried, GRASP_OUT, TheWayBackUnderLoad.FAR, UP, 0.0, None)
        self.assertEqual("reach_way_back_held", harness.failure_code(ctx, CycleFailed("x")))
        self.assertIn("scripted: the gate refuses the retract", str(held.exception))
        self.assertTrue(ctx.labels("run_pose")[-1].startswith("REACH_CARRY"), "nothing moves after the carry")

    def test_a_retract_the_executors_planner_refuses_ends_the_run_where_the_arm_stands(self):
        ctx, ctl = Arm(script={"REACH_RETRACT": cycle.ScanRefused("scripted: scan pose refused by the planner")}), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)
        with self.assertRaises(CycleFailed):
            rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, TheWayBackUnderLoad.FAR, UP, 0.0, None)
        self.assertEqual("reach_retract_refused", harness.failure_code(ctx, CycleFailed("x")))

    def test_a_carry_that_never_left_the_grasp_radius_is_not_retracted(self):
        def refuse(*_args, **_kwargs):
            raise cycle.ScanRefused("scripted: the gate refuses this carry target")
        ctx, ctl = Arm(), ctl_view(proven_orientation_pose=refuse)
        settled = rc.settle(ctx, ctl, "arm_reach", rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0), GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        self.assertFalse(settled.admitted)
        self.assertEqual([], ctx.calls, "nothing was commanded: the arm never left the grasp radius")


    def test_a_carry_that_ended_under_its_floor_is_never_lowered_further_and_the_admission_decides_where_it_stands(self):
        """The block 7.5 mm inside the near edge needs about 58 mm of FK travel: the sagging carry that stopped at its floor
        reached that, so the arm-only check passes; the lowering, which would press the held block into the pad, is not
        asked for, and the proven admission decides on the pose the hold check reads."""
        anchor = (0.0145, -0.140)
        ctx, ctl = Arm(errors=SAG_PAST_95), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        self.assertEqual(rc.LOW, carried.stop)
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, anchor, UP, 0.0, None)
        self.assertNotIn("PLACE lower", ctx.labels("run_pose"))
        self.assertTrue(settled.admitted)
        hold = states(ctx, "RELEASE_HOLD")[-1]
        self.assertEqual((None, [], True), (hold["lower_height_mm"], hold["lower_heights_refused_mm"], hold["lower_skipped"]))
        admission = states(ctx, "RELEASE_ADMISSION")[-1]
        self.assertAlmostEqual(carried.view["tool"]["up_mm"], admission["release_height_mm"], delta=0.05)
        self.assertNotIn("PLACE_LOWER_REFUSED", ctx.states())

    DROOP = {"shoulder_lift": 1.69, "elbow_flex": 2.85}  # the first loaded send of cycle-run-20260920T093143Z

    def test_a_carry_that_ended_under_its_floor_short_of_the_admission_is_never_lowered_and_the_arm_holds_where_it_stands(self):
        """The loaded arm sagged on both lift joints by the recorded re-send errors: the carry ended under its floor and the
        arm-only check refuses. Nothing is lowered; no step of the way back stays short of the executor's arrival abort with
        those errors (TheWayBackUnderLoad), so nothing moves after the carry and the arm holds the cube where it stands."""
        ctx, ctl = Arm(errors=SAG_PAST_95), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        with self.assertRaises(CycleFailed):
            rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        self.assertIn("ARM_ONLY_INSUFFICIENT", ctx.states())
        self.assertNotIn("PLACE lower", ctx.labels("run_pose"))
        self.assertEqual("reach_way_back_held", harness.failure_code(ctx, CycleFailed("x")))
        self.assertTrue(ctx.labels("run_pose")[-1].startswith("REACH_CARRY"))

    # ---- THE LOWERING's out and its predicted height (verify r3 replay MUST-FIX 1) ----
    # The lowering went LOWER_OUT_MM past the carry's MEASURED out, to the release height, with no predicted-height check.
    # For an arm that arrived where it was commanded that is an out no command of the carry had reached (and, after a first
    # send that overshot a short plan by its 18 mm lead, 24 mm past the plan): where the loaded arm sags past an out the carry
    # never commanded (the World's SaggingWorld), the lowering took the tool under the executor's contact floor, the executor
    # stopped all motion, and the run ended with the loaded arm extended.

    INSIDE = (0.0145, -0.080)  # 67.5 mm inside the near edge: the admission takes any travel these tests carry to

    def lowering(self, ctx) -> dict:
        """The tool state the one PLACE lower of the run was commanded to."""
        (target,) = [target for label, target, _start in ctx.commanded if label == "PLACE lower"]
        return target

    def carried_at(self, ctx, commanded_out_mm: float, errors: dict, wanted_out_mm: float) -> rc.Carry:
        """A carry whose last step was commanded to commanded_out_mm at the carry height and measured its lift joints missed
        by `errors` (measured minus commanded), short of wanted_out_mm: the loaded arm (ctx's) where that step left it."""
        start = {**THIRD, **rm.joints_at(cycle, commanded_out_mm - 20.0, cycle.CARRY_HEIGHT_MM)}
        last = cycle.proven_orientation_pose(start, commanded_out_mm / 1000.0, cycle.CARRY_HEIGHT_MM / 1000.0)[0]
        measured = {**THIRD, **{name: float(value) for name, value in last.joints.items()}}
        for name, value in errors.items():
            measured[name] += float(value)
        view = harness.view(measured)
        ctx.current = dict(measured)
        step = {"policy": "arm_reach", "step": 0, "commanded_out_mm": round(commanded_out_mm, 1), "measured_out_mm": round(view["tool"]["out_mm"], 1), "up_mm": round(view["tool"]["up_mm"], 1)}
        return rc.Carry(view, rc.STALLED, "scripted", wanted_out_mm, float(view["tool"]["out_mm"]), (step,), last)

    def test_the_lowering_never_commands_the_loaded_arm_farther_out_than_the_carrys_last_command(self):
        """The proven lowering's 6 mm lead compensates a loaded arm that lags its command: an arm that arrived where the carry
        last commanded it is lowered at that out, never at an out no command of the carry reached."""
        ctx, ctl = Arm(), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        last = carried.steps[-1]["commanded_out_mm"]
        self.assertAlmostEqual(last, carried.out_mm, delta=0.2, msg="the arm realized its last command")
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        lowered = self.lowering(ctx)
        self.assertLessEqual(lowered["out_mm"], last + 0.1)
        self.assertIn(round(lowered["up_mm"], 1), rc.lower_heights_mm(ctl), "to a release height")
        self.assertTrue(settled.admitted)
        self.assertAlmostEqual(last, states(ctx, "RELEASE_HOLD")[-1]["lower_out_mm"], delta=0.1)

    def test_an_arm_that_lags_its_last_command_by_more_than_the_lead_is_lowered_with_the_proven_lead(self):
        """The first loaded send of cycle-run-20260920T103136Z (1.41 and 2.95 deg) lags its command by about 18 mm: lowered
        6 mm past the measured out, as the proven stage lowers, still nearer than the carry's last command."""
        ctx, ctl = Arm(errors=steady(lift=1.41, elbow=2.95)), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 50.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        self.assertGreater(carried.steps[-1]["commanded_out_mm"] - carried.out_mm, rc.LOWER_OUT_MM, "the arm lags its last command")
        self.assertLessEqual(carried.out_mm, 50.0, "and arrived short of the plan")
        rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.INSIDE, UP, 0.0, None)
        self.assertAlmostEqual(carried.out_mm + rc.LOWER_OUT_MM, self.lowering(ctx)["out_mm"], delta=0.2, msg="out + 6 mm, as the proven stage lowers")

    def test_a_first_send_that_overshot_a_short_plan_is_lowered_no_farther_out_than_the_plans_own_lowering(self):
        """The first send leads by the recorded 18 mm first-send shortfall: an arm that realizes it arrives 18 mm past a short
        plan, and the lowering went 6 mm past that. It is now asked where the plan's own lowering is, the plan's out plus the
        proven lead, which brings the block back toward the travel the plan was admitted at as it goes down."""
        ctx, ctl = Arm(), ctl_view()
        carried = self._carried(ctx, ctl, 40.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        self.assertGreater(carried.out_mm, 40.0 + rc.LOWER_OUT_MM, "the first send overshot the plan")
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.INSIDE, UP, 0.0, None)
        self.assertLessEqual(self.lowering(ctx)["out_mm"], 40.0 + rc.LOWER_OUT_MM + 0.1)
        self.assertTrue(settled.admitted)

    def test_a_lowering_height_predicted_to_take_the_lowest_link_under_the_executors_floor_is_not_sent_and_a_higher_one_is(self):
        """The carry's last step measured 3.3 deg on both lift joints at +100 mm commanded (the tool about +9 mm up, above the
        carry's floor): lowered with the same errors, the shoulder_lift's grown by the recorded growth (THE CARRY's own
        prediction), the proven release height and the next would take the lowest link under the floor the executor stops
        all motion at (ctl.CONTACT_FLOOR_M, what run_pose is handed); they are not sent, and the lowering is asked higher."""
        ctx, ctl = Arm(), ctl_view()
        carried = self.carried_at(ctx, 100.0, {"shoulder_lift": 3.3, "elbow_flex": 3.3}, 126.0)
        self.assertGreaterEqual(carried.view["tool"]["up_mm"], rc.floor_mm(ctl))
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.INSIDE, UP, 0.0, None)
        self.assertEqual(["PLACE lower"], ctx.labels("run_pose"), "one lowering is sent")
        hold = states(ctx, "RELEASE_HOLD")[-1]
        self.assertEqual(cycle.PLACE_HEIGHT_MM + 2 * rc.LOWER_RAISE_STEP_MM, hold["lower_height_mm"])
        self.assertEqual([cycle.PLACE_HEIGHT_MM, cycle.PLACE_HEIGHT_MM + rc.LOWER_RAISE_STEP_MM], hold["lower_heights_predicted_under_floor_mm"])
        self.assertAlmostEqual(cycle.PLACE_HEIGHT_MM + 2 * rc.LOWER_RAISE_STEP_MM, self.lowering(ctx)["up_mm"], delta=0.1)
        self.assertTrue(settled.admitted)

    def test_a_lowering_no_height_of_which_is_predicted_above_the_executors_floor_is_never_sent(self):
        """The carry's last step measured 3.9 deg on the shoulder_lift and 1.0 on the elbow at +130 mm commanded (the tool
        about +9 mm up): every release height, lowered with those errors grown, would take the lowest link under the
        executor's floor. Nothing is sent (PLACE_LOWER_REFUSED says why), and the admission decides where the arm stands."""
        ctx, ctl = Arm(), ctl_view()
        carried = self.carried_at(ctx, 130.0, {"shoulder_lift": 3.9, "elbow_flex": 1.0}, 140.0)
        self.assertGreaterEqual(carried.view["tool"]["up_mm"], rc.floor_mm(ctl))
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        self.assertNotIn("PLACE lower", ctx.labels("run_pose"))
        refused = states(ctx, "PLACE_LOWER_REFUSED")[-1]
        self.assertEqual(list(rc.lower_heights_mm(ctl)), refused["heights_predicted_under_floor_mm"])
        self.assertTrue(all(value < cycle.CONTACT_FLOOR_M for value in refused["predicted_lowest_link_m"]), refused)
        hold = states(ctx, "RELEASE_HOLD")[-1]
        self.assertIsNone(hold["lower_height_mm"])
        admission = states(ctx, "RELEASE_ADMISSION")[-1]
        self.assertAlmostEqual(carried.view["tool"]["up_mm"], admission["release_height_mm"], delta=0.05, msg="decided where the arm stands")
        self.assertTrue(settled.admitted)

    def test_a_carry_that_measured_no_error_is_lowered_as_if_the_lowering_took_the_largest_loaded_errors_on_record(self):
        """The World's DroopingWorld: the lowering alone droops, after a carry that measured no error. Far out, the proven
        release height with the largest loaded errors on record (4.07 deg shoulder_lift, 2.95 elbow_flex) would take the
        lowest link under the executor's floor: it is not sent, and the lowering is asked higher."""
        ctx, ctl = Arm(), ctl_view()
        carried = self._carried(ctx, ctl, 114.0)
        self.assertEqual({}, {name: round(value, 6) for name, value in rc._errors_against(ctl, carried.view, carried.last_pose).items() if abs(value) > 1e-6})
        rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        hold = states(ctx, "RELEASE_HOLD")[-1]
        self.assertIn(cycle.PLACE_HEIGHT_MM, hold["lower_heights_predicted_under_floor_mm"])
        self.assertGreater(hold["lower_height_mm"], cycle.PLACE_HEIGHT_MM)
        self.assertEqual({"shoulder_lift": 4.07, "elbow_flex": 2.95}, {name: hold["lower_errors_deg"][name] for name in ("shoulder_lift", "elbow_flex")})

    def test_the_lowerings_errors_are_the_carrys_own_each_at_least_the_largest_loaded_error_on_record(self):
        ctl = ctl_view()
        self.assertEqual({"shoulder_lift": 4.07, "elbow_flex": 2.95}, rc.LOADED_ERROR_RECORD_DEG)
        low = rc.lowering_errors(ctl, {"shoulder_lift": 1.69, "elbow_flex": 2.85, "wrist_flex": -0.2})
        self.assertEqual((4.07, 2.95, -0.2), (low["shoulder_lift"], low["elbow_flex"], low["wrist_flex"]), "the record where the carry measured less")
        high = rc.lowering_errors(ctl, {"shoulder_lift": 3.3, "elbow_flex": 3.3})
        self.assertAlmostEqual(3.3 + rc.LIFT_GROWTH_DEG, high["shoulder_lift"], delta=1e-9, msg="THE CARRY's grown error where it is more")
        self.assertAlmostEqual(3.3, high["elbow_flex"], delta=1e-9)

    def test_the_lowerings_prediction_is_the_lowest_link_of_the_controllers_fk_of_the_missed_target(self):
        ctx, ctl = Arm(), ctl_view()
        carried = self.carried_at(ctx, 100.0, {"shoulder_lift": 1.69, "elbow_flex": 2.85}, 126.0)
        pose = cycle.proven_orientation_pose(carried.view["measured"], 0.090, cycle.PLACE_HEIGHT_MM / 1000.0)[0]
        errors = {"shoulder_lift": 4.07, "elbow_flex": 2.95, "wrist_flex": 0.0}
        missed = {**carried.view["measured"], **pose.joints, "shoulder_lift": pose.joints["shoulder_lift"] + 4.07, "elbow_flex": pose.joints["elbow_flex"] + 2.95}
        self.assertAlmostEqual(cycle.tool_state(missed)["lowest_link_m"], rc.predicted_lowest_link_m(ctl, pose, errors), delta=1e-12)


class TheWayBackUnderLoad(unittest.TestCase):
    """THE WAY BACK UNDER LOAD (reach_carry; the lead's decision A of 2026-10-01, verify r5 safety MF1). After a refusal the
    loaded arm comes back in bounded steps, each one inside what the records show the loaded arm doing, read between steps,
    and the arm HOLDS where it stands (the gripper closed, reach_way_back_held) whenever a step would leave that envelope or
    measured a joint error, a sag or no progress. FAR lies 52.5 mm outside the near edge: the admission needs 155 mm of FK
    travel, so a carry to 114 mm arrives and the arm-only check refuses it at the carry height."""

    FAR = (0.0145, -0.200)
    ANCHOR = TheSettle.ANCHOR
    DROOP = TheSettle.DROOP

    def setUp(self):
        fixtures.use_synthetic_runtime(self)

    def failure(self, ctx) -> tuple:
        """(code, sentence, values) of the last refusal ctx.failed minted."""
        code, sentence, values = [args[:3] for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1]
        return code, sentence, values

    def way_back(self, ctx) -> list:
        """Every move of the way back: (label, start joints, target joints, realized joints)."""
        return [move for move in ctx.moves if move[0].startswith("REACH_RETRACT")]

    def settle_far(self, ctx, ctl):
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        return rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.FAR, UP, 0.0, None)

    def assertInsideTheEnvelope(self, ctx):
        """Every commanded move of the way back, against the move before it (the last command): the lead's per-step bounds
        (shoulder_lift at most 8 deg, the other joints within the largest change recorded in one raise of the extended arm),
        never farther out than the tool measured before it nor than the last command (a step back always nearer), back toward
        the body never more than WAY_BACK_SAG_MM above the measured tool (verify r7 safety MF1), every lift joint short of the
        executor's arrival abort should it miss its target by the larger of its last error and its whole change (the
        shoulder_lift's grown by the recorded growth), the tool at most WAY_BACK_STALL_DROP_MAX_MM lower should the loaded
        lift not move at all, the held block above the pad's top under every model of the loaded lift (models_up_mm), at
        every out, and the move as realized short of the arrival abort."""
        checked = 0
        for index, (label, start, target, realized) in enumerate(ctx.moves):
            if not label.startswith("REACH_RETRACT"):
                continue
            checked += 1
            last = ctx.moves[index - 1][2] if index else None
            missed = {name: (start[name] - last[name]) if last else 0.0 for name in cycle.LIFT_JOINTS}
            change = {name: target[name] - start[name] for name in cycle.LIFT_JOINTS}
            self.assertLessEqual(abs(change["shoulder_lift"]), rc.WAY_BACK_LIFT_STEP_MAX_DEG + 1e-9, label)
            for name, bound in rc.WAY_BACK_JOINT_STEP_MAX_DEG.items():
                self.assertLessEqual(abs(change[name]), bound + 1e-9, label)
            for name in cycle.LIFT_JOINTS:
                self.assertLess(max(abs(missed[name]), abs(change[name])) + (rc.LIFT_GROWTH_DEG if name == "shoulder_lift" else 0.0), rc.EXECUTOR_ARRIVAL_DEG, f"{label} {name}")
            before, aimed = cycle.tool_state(start), cycle.tool_state({**start, **target})
            commanded = cycle.tool_state({**start, **last}) if last else before
            self.assertLessEqual(aimed["out_mm"], commanded["out_mm"] + 0.05, f"{label} commanded the loaded arm farther out than its last command")
            self.assertLessEqual(aimed["out_mm"], before["out_mm"] + 0.05, f"{label} commanded the loaded arm farther out than it measured")
            if "back toward the body" in label:
                self.assertLess(aimed["out_mm"], min(commanded["out_mm"], before["out_mm"]) - 0.5, f"{label} is a step back of the measured tool")
                self.assertLessEqual(aimed["up_mm"], before["up_mm"] + rc.WAY_BACK_SAG_MM + 0.05, f"{label} raised the loaded arm at extension")
            models = self.models_up_mm(start, missed, target)
            self.assertGreaterEqual(models["stalled"], max(rc.PAD_THICKNESS_MM, before["up_mm"] - rc.WAY_BACK_STALL_DROP_MAX_MM) - 0.05, label)
            for name, up in models.items():  # at every out (the lead's ruling of 2026-10-02 on verify r10 safety MF1)
                self.assertGreaterEqual(up, rc.PAD_THICKNESS_MM - 0.05, f"{label}: the {name} model of the loaded lift would take the block under the pad's top")
            self.assertLess(max(abs(realized[name] - target[name]) for name in cycle.LIFT_JOINTS), rc.EXECUTOR_ARRIVAL_DEG, f"{label} would end in the executor's arrival abort")
        self.assertTrue(checked, "the way back commanded nothing")
        return checked

    @staticmethod
    def models_up_mm(measured: dict, missed: dict, target: dict) -> dict:
        """The held block's height, mm above the grasp height, at the end of a step to `target` under each model of the loaded
        lift (spelled here, not read from reach_carry): `realized`, every lift joint at its target plus the error it measures
        against the last command (`missed`), the shoulder_lift's grown by the recorded growth (THE CARRY's prediction);
        `stalled`, the shoulder_lift where it stands (the loaded raises at extension on record that did not rise), the other
        lift joints at their targets plus their errors; `further`, the same with the shoulder_lift sagged by the recorded growth
        from where it stands (the two recorded loaded raises at extension that sagged further, +1.85 and +1.49 deg)."""
        others = {name: float(target[name]) + float(missed.get(name, 0.0)) for name in ("elbow_flex", "wrist_flex")}
        realized = {**measured, **others, "shoulder_lift": float(target["shoulder_lift"]) + float(missed.get("shoulder_lift", 0.0)) + rc.LIFT_GROWTH_DEG}
        stalled = {**measured, **others, "shoulder_lift": float(measured["shoulder_lift"])}
        further = {**measured, **others, "shoulder_lift": float(measured["shoulder_lift"]) + rc.LIFT_GROWTH_DEG}
        return {name: cycle.tool_state(joints)["up_mm"] for name, joints in (("realized", realized), ("stalled", stalled), ("further", further))}

    def first_step_back(self, out: float, height: float, lag=(0.0, 0.0)):
        """(the pose the arm measures, the lowering it was last commanded, the way back's first step back from there): a block
        lowered to `height` mm at `out` mm, the arm missing the lowering by `lag` (shoulder_lift, elbow_flex, deg; positive is
        nearer and lower, as the loaded arm misses on record)."""
        lowering = cycle.proven_orientation_pose({**THIRD, **rm.joints_at(cycle, out - 20.0, cycle.CARRY_HEIGHT_MM)}, out / 1000.0, height / 1000.0)[0]
        measured = {**THIRD, **{name: float(value) for name, value in lowering.joints.items()}}
        measured["shoulder_lift"] += lag[0]
        measured["elbow_flex"] += lag[1]
        return measured, lowering, rc.plan_way_back_step(ctl_view(), harness.view(measured), lowering, rc.BACK, GRASP_OUT)

    # verify r7 safety MF1: the way back was planned from the LAST COMMAND, so from a loaded arm that lags it (every sagging
    # far carry) its first steps were commanded 6 to 17 mm OUT of the measured tool and 7 to 16 mm UP, a re-send of nearly
    # the far carry target; the records show such loaded re-sends at extension sagging further. The recorded loaded errors
    # (measured minus commanded, shoulder_lift and elbow_flex, deg): none, a small steady lag, the first sends and the
    # re-sends of cycle-run-20260920T093143Z and T103136Z, and the scripted Worlds' sags.
    RECORDED_LAGS = ((0.0, 0.0), (0.3, 0.3), (1.0, 1.0), (1.41, 2.95), (1.69, 2.85), (2.5, 2.5), (3.54, 2.67))

    def test_no_step_is_commanded_out_of_the_measured_tool_nor_up_at_extension_under_every_recorded_loaded_error(self):
        """Every step THE WAY BACK UNDER LOAD would send, from a loaded arm that missed its last command (a carry step at the
        carry height, or a lowering) by a recorded loaded error: never farther out than the tool measures, and, back toward
        the body, never more than WAY_BACK_SAG_MM above it. A lagging loaded arm that has no such step holds before any move."""
        ctl, sent, held = ctl_view(), 0, 0
        for out in (90.0, 116.6, 129.3, 150.0):
            for height in (cycle.CARRY_HEIGHT_MM, 8.0):
                last = cycle.proven_orientation_pose({**THIRD, **rm.joints_at(cycle, out - 20.0, height)}, out / 1000.0, height / 1000.0)[0]
                for lift, elbow in self.RECORDED_LAGS:
                    measured = {**THIRD, **{name: float(value) for name, value in last.joints.items()}}
                    measured["shoulder_lift"] += lift
                    measured["elbow_flex"] += elbow
                    view = harness.view(measured)
                    for phase in (rc.BACK, rc.UP):
                        step = rc.plan_way_back_step(ctl, view, last, phase, GRASP_OUT)
                        if step.pose is None:
                            held += phase == rc.BACK
                            continue
                        sent += phase == rc.BACK
                        aimed = cycle.tool_state({**measured, **{name: float(value) for name, value in step.pose.joints.items()}})
                        with self.subTest(out=out, height=height, lift=lift, elbow=elbow, phase=phase):
                            self.assertLessEqual(aimed["out_mm"], view["tool"]["out_mm"] + 0.05, "commanded out of the measured tool")
                            if phase == rc.BACK:
                                self.assertLessEqual(aimed["up_mm"], view["tool"]["up_mm"] + rc.WAY_BACK_SAG_MM + 0.05, "a raise at extension")
        self.assertTrue(sent and held, (sent, held))

    def test_its_bounds_are_the_leads_and_the_records(self):
        self.assertEqual(8.0, rc.WAY_BACK_LIFT_STEP_MAX_DEG)
        self.assertEqual({"elbow_flex": 28.26, "wrist_flex": 13.09}, rc.WAY_BACK_JOINT_STEP_MAX_DEG)
        import scan_world  # the executor's own arrival check
        self.assertEqual(scan_world.ARRIVAL_TOLERANCE_DEG, rc.EXECUTOR_ARRIVAL_DEG)

    def test_an_arm_that_realizes_its_targets_comes_back_in_bounded_steps_never_farther_out_than_it_measures(self):
        ctx, ctl = Arm(), ctl_view()
        settled = self.settle_far(ctx, ctl)
        self.assertFalse(settled.admitted)
        self.assertEqual(rc.CARRY_SHORT, settled.refusal)
        self.assertLessEqual(settled.view["tool"]["out_mm"] - GRASP_OUT, rc.RETRACT_WITHIN_MM)
        moves = self.way_back(ctx)
        self.assertInsideTheEnvelope(ctx)
        self.assertLessEqual(len(moves), rc.WAY_BACK_MAX_STEPS)
        steps = states(ctx, "REACH_RETRACT_STEP")
        self.assertEqual(len(moves), len(steps))
        self.assertEqual(len(moves), states(ctx, "REACH_RETRACT")[-1]["steps"])

    def test_an_arm_that_lags_its_carry_commands_is_held_before_any_step(self):
        """verify r7 safety MF1. A loaded arm 1 deg off on both lift joints stands about 7 mm short of and 7 mm under its last
        carry command. Planned from that command, the way back's first step was commanded about 6 mm OUT of the measured tool
        and 7 mm UP, toward the far carry target (a re-send in substance, which the records show sagging further at
        extension). Planned from the measured tool, every step back asks the lift to take up the lag the stall bound refuses
        (a loaded lift that does not move would drop the block more than WAY_BACK_STALL_DROP_MAX_MM): nothing is sent after
        the carry and the arm holds the cube where it stands."""
        ctx, ctl = Arm(errors=steady(lift=1.0, elbow=1.0), way_back_errors={"shoulder_lift": 1.0, "elbow_flex": 1.0}), ctl_view()
        with self.assertRaises(CycleFailed):
            self.settle_far(ctx, ctl)
        code, _sentence, values = self.failure(ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_NO_STEP), (code, values["held_by"]))
        self.assertEqual([], self.way_back(ctx))
        self.assertTrue(ctx.labels("run_pose")[-1].startswith("REACH_CARRY"), "nothing moves after the carry")

    def test_after_a_lowering_it_goes_back_at_the_low_height_first_and_rises_only_near_the_body(self):
        """A short plan (40 mm) is overshot by the first send's lead and lowered at the plan's own lowering, 12 mm up: the
        travel the lowering leaves is short of what this anchor needs (31 mm), and the release is refused on its margin,
        the block above the pad's top under every model of the loaded lift (from a block that stands lower, or a crept one,
        nothing is sent: test_a_hold_that_creeps_is_not_released_and_the_crept_block_is_never_moved,
        test_a_block_lowered_to_the_release_height_is_held_where_it_stands_and_a_higher_one_inside_the_reach_takes_its_way_back).
        The way back goes back at the height the lowering commanded, then rises to the carry height near the grasp radius."""
        ctx, ctl = Arm(), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 40.0)
        self.assertEqual(rc.ARRIVED, carried.stop)
        with mock.patch.object(rc, "lower_heights_mm", lambda ctl: (12.0,)):
            settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, (0.0145, -0.123), UP, 0.0, None)
        self.assertFalse(settled.admitted)
        self.assertEqual(rc.MARGIN_SHORT, settled.refusal)
        self.assertInsideTheEnvelope(ctx)
        lowered = [target for label, target, _start in ctx.commanded if label == "PLACE lower"][-1]
        kinds = [entry["phase"] for entry in states(ctx, "REACH_RETRACT_STEP")]
        self.assertEqual(sorted(kinds, key=("back", "up").index), kinds, "every back step comes before every rise")
        for entry in states(ctx, "REACH_RETRACT_STEP"):
            if entry["phase"] == "back":
                self.assertAlmostEqual(lowered["up_mm"], entry["commanded_up_mm"], delta=0.2, msg="back at the height the lowering commanded")
            else:
                self.assertLessEqual(entry["before_out_mm"] - GRASP_OUT, rc.RETRACT_WITHIN_MM + 0.05, "a rise only near the grasp radius")
        self.assertIn("up", kinds)
        self.assertAlmostEqual(cycle.CARRY_HEIGHT_MM, states(ctx, "REACH_RETRACT_STEP")[-1]["commanded_up_mm"], delta=0.05)
        self.assertLessEqual(settled.view["tool"]["out_mm"] - GRASP_OUT, rc.RETRACT_WITHIN_MM)

    def test_a_loaded_raise_the_lift_does_not_realize_past_95_mm_out_holds_the_arm_after_one_bounded_step(self):
        """verify r5 safety MF1, the records: no loaded raise at +99.6 to +112.2 mm out rose. The lift here keeps its value
        whenever a move of the way back asks it to rise from past +95 mm out: the first step moves the held block at most
        WAY_BACK_STALL_DROP_MAX_MM down, never into the executor's arrival abort, and the sag it measured holds the arm
        there, the gripper closed."""
        ctx, ctl = Arm(stall_from_out_mm=95.0), ctl_view()
        with self.assertRaises(CycleFailed):
            self.settle_far(ctx, ctl)
        code, sentence, values = self.failure(ctx)
        self.assertEqual("reach_way_back_held", code)
        moves = self.way_back(ctx)
        self.assertEqual(1, len(moves), "one step, then the hold")
        self.assertInsideTheEnvelope(ctx)
        (_label, start, _target, realized) = moves[0]
        self.assertLessEqual(cycle.tool_state(start)["up_mm"] - cycle.tool_state(realized)["up_mm"], rc.WAY_BACK_STALL_DROP_MAX_MM + 0.05)
        self.assertIn(values["held_by"], (rc.HELD_SAG, rc.HELD_JOINT_ERROR))
        self.assertIn("the gripper stays closed", sentence)
        self.assertEqual([], [call for call in ctx.calls if call[0] == "grip"], "the gripper is never commanded")

    def test_a_held_block_lowered_under_the_pads_top_is_never_moved(self):
        """Lowered with the recorded first-send droop the held block measures under the pad's top: any step back or up with
        a lift that does not move would press it further or drag it along the pad. Nothing is sent; the arm holds."""
        ctx, ctl = Arm(lower_errors=self.DROOP), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.1)
        with self.assertRaises(CycleFailed):
            rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        code, _sentence, values = self.failure(ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_NO_STEP), (code, values["held_by"]))
        self.assertEqual("PLACE lower", ctx.labels("run_pose")[-1], "nothing moves after the lowering")
        self.assertLess(values["measured_up_mm"], rc.PAD_THICKNESS_MM)

    # The loaded arm lowered for a release: one that realized its lowering, one that lags it on the shoulder_lift alone, and
    # one that lags it on both lift joints as the record shows inside the proven loaded reach (the descent of the 2026-09-20
    # base placements, 0.8 and 2.2 deg).
    LOWERED_LAGS = ((0.0, 0.0), (0.8, 0.0), (0.8, 2.2))
    # Which model of the loaded lift a refused candidate names (way_back_bounds' sentences).
    MODEL_WORDS = {"realize the step": "realized", "not move": "stalled", "sag further": "further"}

    def test_no_step_is_sent_that_any_model_of_the_loaded_lift_puts_under_the_pads_top_at_any_out(self):
        """verify r10 safety MF1 (the lead's ruling of 2026-10-02; verify r6 safety MF1 before it, past +95 mm out). From a
        block lowered inside the proven loaded reach the way back sent steps that its own models put under the pad's top:
        realizing the step with the errors it measures and the recorded growth (held to the executor's floor alone), or the
        lift sagging further as two recorded loaded raises at extension did (held to the pad's top past +95 mm out only); a
        loaded lift that does not move was the only model held to the pad there, and no loaded raise between +45.5 and +95
        mm out is on record to prefer it. Every model is now held to the pad's top at every out: every first step sent from
        a lowered block, inside and past the proven loaded reach, leaves the held block above it under all three (spelled
        here, models_up_mm), and each of the three models is the one that refuses some candidate."""
        sent, held, binding = 0, 0, set()
        for out in (40.0, 60.0, 80.0, 95.0, 110.0, 126.0):
            for height in (8.0, 12.0, 14.0):
                for lag in self.LOWERED_LAGS:
                    measured, lowering, step = self.first_step_back(out, height, lag)
                    binding.update(model for _out, _up, why in step.refused for words, model in self.MODEL_WORDS.items() if words in why)
                    if step.pose is None:
                        held += 1
                        continue
                    sent += 1
                    missed = {name: measured[name] - float(lowering.joints[name]) for name in cycle.LIFT_JOINTS}
                    models = self.models_up_mm(measured, missed, {name: float(value) for name, value in step.pose.joints.items()})
                    with self.subTest(out=out, height=height, lag=lag):
                        for name, up in models.items():
                            self.assertGreaterEqual(up, rc.PAD_THICKNESS_MM - 1e-6, f"the {name} model ends the held block {up:+.1f} mm up")
                            self.assertAlmostEqual(up, step.model_up_mm[name], delta=0.05, msg=f"the step states its {name} model")
        self.assertTrue(sent and held, (sent, held))
        self.assertEqual({"realized", "stalled", "further"}, binding, "each model of the loaded lift refuses some step")

    def test_a_block_lowered_to_the_release_height_is_held_where_it_stands_and_a_higher_one_inside_the_reach_takes_its_way_back(self):
        """verify r10 safety MF1 (the lead's ruling of 2026-10-02) over verify r7 replay MF2, which applied the sag-further
        bound past the proven loaded reach only so that a release refused after a lowering inside it would reach the in-run
        fallback. Lowered to the proven release height, the held block stands where every step back would end it under the
        pad's top should the loaded lift sag further by the recorded growth, at every out: an arm that realized its lowering
        exactly is held where it stands before any step, the cube held, as the installed controller ends its own refused
        release. From a lowering higher up inside the proven loaded reach no model takes the block to the pad, and the way
        back steps."""
        for out in (60.0, 80.0, 90.0, 95.0, 100.0, 110.0, 120.0, 126.0):
            _measured, _lowering, low = self.first_step_back(out, cycle.PLACE_HEIGHT_MM)
            with self.subTest(out=out, height=cycle.PLACE_HEIGHT_MM):
                self.assertIsNone(low.pose, "a block lowered to the release height is held before any step")
                self.assertIn("sag further", low.refused[-1][2])
            if out <= cycle.ARM_LOADED_REACH_OUT_MM:
                _measured, _lowering, high = self.first_step_back(out, 14.0)
                with self.subTest(out=out, height=14.0):
                    self.assertIsNotNone(high.pose, high.refused[-1:])
                    self.assertGreaterEqual(min(high.model_up_mm.values()), rc.PAD_THICKNESS_MM)

    def test_a_lift_error_that_leaves_no_step_inside_the_records_holds_before_anything_moves(self):
        """Both lift joints 3.9 deg off past +95 mm commanded (the shape of the recorded re-sends): a step planned from the
        measured tool asks the wrist to take up the 7.8 deg the sag tilted the tool, more than any recorded raise of the
        extended arm changed it (and with the 1.85 deg the loaded error grew in one send on record, any step from the last
        command could end past the executor's 5 deg arrival check). Nothing is sent after the carry; the arm holds."""
        ctx, ctl = Arm(errors=SAG_PAST_95), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        self.assertEqual(rc.LOW, carried.stop)
        with self.assertRaises(CycleFailed):
            rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, self.ANCHOR, UP, 0.0, None)
        code, sentence, values = self.failure(ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_NO_STEP), (code, values["held_by"]))
        self.assertTrue(ctx.labels("run_pose")[-1].startswith("REACH_CARRY"), "nothing moves after the carry")
        self.assertIn("recorded in one raise of the extended arm", sentence)

    def test_a_step_that_measures_a_joint_error_holds_the_arm(self):
        ctx, ctl = Arm(way_back_errors={"shoulder_lift": 4.5}), ctl_view()
        with self.assertRaises(CycleFailed):
            self.settle_far(ctx, ctl)
        code, _sentence, values = self.failure(ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_JOINT_ERROR), (code, values["held_by"]))
        self.assertEqual(1, len(self.way_back(ctx)))

    def test_a_step_that_sags_holds_the_arm(self):
        ctx, ctl = Arm(way_back_errors={"elbow_flex": 2.5}), ctl_view()
        with self.assertRaises(CycleFailed):
            self.settle_far(ctx, ctl)
        code, _sentence, values = self.failure(ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_SAG), (code, values["held_by"]))
        self.assertEqual(1, len(self.way_back(ctx)))

    def test_a_step_that_did_not_bring_the_arm_back_holds_it(self):
        ctx, ctl = Arm(script={"REACH_RETRACT": lambda ctx, *args, **kwargs: harness.view(ctx.current)}), ctl_view()
        with self.assertRaises(CycleFailed):
            self.settle_far(ctx, ctl)
        code, _sentence, values = self.failure(ctx)
        self.assertEqual(("reach_way_back_held", rc.HELD_REGRESSION), (code, values["held_by"]))

    def test_a_hold_says_why_the_in_run_fallback_does_not_run(self):
        """The lead's ruling of 2026-10-02 on operator question Q7 (A, keep the hold; verify r10 replay MUST-FIX 2 and policy
        MF1): where the way back cannot bring the loaded arm to the carry pose inside the recorded envelope, the arm holds
        where it stands and the run ends for the operator, and decision 3's in-run fallback, which starts only from the carry
        pose, does not run. The record says so in plain words and in its values, whichever check held the arm: before any
        step (no_step) or after one (sag, joint_error, regression)."""
        for scene, arm in (("before any step", Arm(errors=steady(lift=1.0, elbow=1.0), way_back_errors={"shoulder_lift": 1.0, "elbow_flex": 1.0})),
                           ("after a step that sagged", Arm(way_back_errors={"elbow_flex": 2.5})), ("after a step with a joint error", Arm(way_back_errors={"shoulder_lift": 4.5}))):
            with self.subTest(scene=scene):
                with self.assertRaises(CycleFailed) as raised:
                    self.settle_far(arm, ctl_view())
                code, sentence, values = self.failure(arm)
                self.assertEqual("reach_way_back_held", code)
                self.assertEqual(sentence, str(raised.exception), "the sentence the operator reads is the record's")
                self.assertIn("the gripper stays closed and the cube held, for the operator to take; the in-run fallback", sentence)
                self.assertIn("does not run: it starts only from the carry pose", sentence)
                self.assertEqual("not_run", values["in_run_fallback"])

    def test_a_way_back_that_runs_out_of_steps_still_out_is_incomplete_and_holds_where_it_stands(self):
        ctx, ctl = Arm(), ctl_view()
        with mock.patch.object(rc, "WAY_BACK_MAX_STEPS", 3):
            with self.assertRaises(CycleFailed):
                self.settle_far(ctx, ctl)
        self.assertEqual("reach_retract_incomplete", self.failure(ctx)[0])
        self.assertEqual(3, len(self.way_back(ctx)))


class TheRelease(unittest.TestCase):
    def setUp(self):
        fixtures.use_synthetic_runtime(self)

    def test_it_is_the_proven_release_retreat_exit_and_placed(self):
        inside = harness.cube_record([515, 332, 595, 419], cube_center_pad_m=[0.0145, -0.080], observation_id="exit-view")
        inside["cube_by_template"]["inside_pad_outline"] = True
        ctx, ctl = Arm(script={"perception:EXIT": [[inside]]}), ctl_view()
        carried = rc.carry(ctx, ctl, "arm_reach", harness.view(THIRD), GRASP_OUT, 114.0)
        settled = rc.settle(ctx, ctl, "arm_reach", carried, GRASP_OUT, TheSettle.ANCHOR, UP, 0.0, None)
        summary = {"placed": False, "clearance_target_met": False, "footprint_clearance_mm": None}
        outcome = rc.release_and_place(ctx, ctl, summary, settled, TheSettle.ANCHOR, UP, 0.0, cycle.BASE_RATE_PRIOR_MM_S, UP)
        self.assertEqual(["grip"], [kind for kind, label in ctx.calls if kind == "grip"])
        self.assertEqual(["RETREAT back", "EXIT to the low scan pose", "SETTLE goals on the measured pose"], ctx.labels("run_pose")[-3:])
        self.assertTrue(summary["placed"] and summary["clearance_target_met"])
        self.assertEqual((0.0, list(TheSettle.ANCHOR), UP), (outcome.advanced, list(outcome.anchor), outcome.axis))
        self.assertAlmostEqual(cycle.placed_margin_mm([0.0145, -0.080]), outcome.margin, delta=1e-9)
        order = [ctx.states().index(name) for name in ("RELEASE_ADMISSION", "RELEASED", "PLACED")]
        self.assertEqual(sorted(order), order)
        self.assertEqual([True, True], [ctx.events.predicates(name)[-1]["value"] for name in ("placed", "clearance_target_met")])


class TheModule(unittest.TestCase):
    def test_it_never_imports_the_controller_and_names_its_stops(self):
        source = (ROOT / "autonomy" / "manipulation" / "reach_carry.py").read_text()
        self.assertNotIn("import run_single_cube_cycle", source)
        self.assertEqual((rc.ARRIVED, rc.REGRESSION, rc.STALLED, rc.JOINT_ERROR, rc.LOW, rc.GATE_REFUSAL, rc.CAP, rc.STEPS), rc.STOPS)
        self.assertEqual(cycle.PLACE_HEIGHT_MM, rc.floor_mm(ctl_view()), "the carry's floor is the proven release height")
        self.assertGreater(rc.floor_mm(ctl_view()), 3.0, "above the pad, which is about 3 mm thick")
        self.assertLess(rc.JOINT_ERROR_MAX_DEG, cycle.ARRIVAL_TOLERANCE_DEG, "the carry's stop lies under the executor's own abort (an error that grows past "
                        "the abort within one step is the executor's, a named residual)")
        self.assertLessEqual(rc.STEP_MAX_MM + 0.0, cycle.CARRY_MAX_TOOL_TRAVEL_M * 1000.0)
        self.assertTrue(math.isfinite(rc.HOLD_CHECK_S) and rc.HOLD_CHECK_S > 0.0)


if __name__ == "__main__":
    unittest.main()
