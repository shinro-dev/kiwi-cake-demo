#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_placement_executor.py: the placement once the cube is held under the additive placement words
# (autonomy/manipulation/placement_executor.py) and the controller's window that lends the frozen advance loop another
# placement rule for one advance (run_single_cube_cycle._advance_rule). What is pinned, pure (the end-to-end runs are
# test_reach_policies_execution.py):
#   - the executor is engaged by an additive placement word only (multi_block alone, the placement track's words and no
#     word leave the proven stage to place the cube);
#   - what a run may execute is the proven stage's own rule for the mode of a held cube: a mission target runs the mode it
#     was bound to, the one flip is an auto target bound ARM_ONLY in a run that carries the capability, a run that carries
#     the capability is never placed arm-only, a LIVE run without a mission target never drives the base;
#   - the rule window: inside it the controller's base_advance_needed_mm IS the lent rule, so the frozen loop's call by name
#     and the capability's approach state both read it; outside it, and after an error inside it, it is the proven
#     function; a window never opens inside another;
#   - the proven continuation's rule (the proven rule held to the budget this run has left) keeps the frozen loop's own
#     guarantee for the whole run: a pulse that carries the run's measured advance past the 120 mm budget, the last pulse
#     included, ends the advance there with the cube held and nothing further commanded, as the proven loop alone does;
#     and a need the budget left does not cover is refused before any pulse;
#   - so does advance_then_reach's own rule when the fallback asks it again (a further advance after `prior` mm: verify r9
#     safety MF1), the base charged the path it travelled, never its progress along the axis alone; the run's first advance
#     is held to the budget by the frozen loop's own check, as with the proven rule;
#   - the proven policy's candidate after a measured advance whose anchor lies inside the proven arm-only band is the proven
#     rule's base branch (its base advance from there, a release at about the grasp radius), in a run that may drive the
#     base, whatever mode it was bound to; a proven plan that moves the base goes on unchanged;
#   - THE PROVEN TIE under THE LOADED REACH CAP (placement_policy): a block the proven policy chose at SEARCH is never handed
#     to an additive arm-alone plan, and whether the proven policy placed it at SEARCH is read from SEARCH's decision;
#   - THE IN-RUN FALLBACK asks advance_then_reach again, once, after its own attempt (its re-plan with a pulsed advance),
#     before the proven policy, named alone too, and at SEARCH's capability gate the same way; the exhaustion names the
#     policies it asked and the budget left.
# No robot, no camera, no network. Run directly: python3 autonomy/tests/test_placement_executor.py
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))
sys.path.insert(0, str(ROOT / "bin" / "laptop" / "manipulation"))

import placement_executor as pe  # noqa: E402
import placement_policy as pp  # noqa: E402
import placement_selector  # noqa: E402
import policy_advance_then_reach  # noqa: E402
import policy_arm_reach  # noqa: E402
import reach_carry as rc  # noqa: E402
import run_single_cube_cycle as current  # noqa: E402
from stage_policy import CycleFailed  # noqa: E402

PROVEN_RULE = current.base_advance_needed_mm
ANCHOR, AXIS = (0.0145, -0.1825), (0.0, 1.0)


class Target:
    def __init__(self, mode, auto, carries):
        self.mode, self.auto, self.carries_capability = mode, auto, carries


class Ctx:
    def __init__(self, target=None, live=False):
        self.target, self.runtime_live = target, live


def plan(mode):
    return pp.Plan("arm_reach" if mode == pp.MODE_ARM_ONLY else "advance_then_reach", True, mode, 0.0 if mode == pp.MODE_ARM_ONLY else 20.0, 60.0, 90.0, 40.0, 40.0, "a plan")


class TheEngagement(unittest.TestCase):
    def test_only_an_additive_placement_word_engages_it(self):
        for words, engaged in (((), False), (("projected",), False), (("multi_block",), False), (("scene_scan", "search_sweep"), False), (("arm_reach",), True),
                               (("advance_then_reach",), True), (("projected", "arm_reach", "multi_block"), True)):
            with self.subTest(words=words):
                self.assertEqual(engaged, pe.engaged(words))


class WhatARunMayExecute(unittest.TestCase):
    def test_a_run_without_a_mission_target_drives_the_base_only_when_it_is_not_live(self):
        for live in (False, True):
            with self.subTest(live=live):
                ctx = Ctx(live=live)
                self.assertTrue(pe.may_execute(ctx, plan(pp.MODE_ARM_ONLY), None))
                self.assertEqual(not live, pe.may_execute(ctx, plan(pp.MODE_BASE_ADVANCE), None))
                self.assertEqual(not live, pe.may_drive_the_base(ctx))

    def test_a_mission_target_runs_the_mode_it_was_bound_to_with_the_proven_stages_one_flip(self):
        cases = (  # (declared mode, auto, carries the capability, bound mode, plan mode, may execute)
            (pp.MODE_ARM_ONLY, False, False, pp.MODE_ARM_ONLY, pp.MODE_ARM_ONLY, True),
            (pp.MODE_ARM_ONLY, False, False, pp.MODE_ARM_ONLY, pp.MODE_BASE_ADVANCE, False),
            (pp.MODE_BASE_ADVANCE, False, True, pp.MODE_BASE_ADVANCE, pp.MODE_BASE_ADVANCE, True),
            (pp.MODE_BASE_ADVANCE, False, True, pp.MODE_BASE_ADVANCE, pp.MODE_ARM_ONLY, False),
            ("auto", True, False, pp.MODE_ARM_ONLY, pp.MODE_BASE_ADVANCE, False),
            ("auto", True, True, pp.MODE_ARM_ONLY, pp.MODE_BASE_ADVANCE, True),
            ("auto", True, True, pp.MODE_BASE_ADVANCE, pp.MODE_ARM_ONLY, False),
            ("auto", True, True, pp.MODE_BASE_ADVANCE, pp.MODE_BASE_ADVANCE, True),
        )
        for declared, auto, carries, bound, mode, expected in cases:
            with self.subTest(declared=declared, carries=carries, bound=bound, mode=mode):
                ctx = Ctx(Target(declared, auto, carries))
                self.assertEqual(expected, pe.may_execute(ctx, plan(mode), bound))
                self.assertEqual(carries, pe.may_drive_the_base(ctx))


class TheRuleWindow(unittest.TestCase):
    def test_inside_the_window_the_frozen_callers_read_the_lent_rule_and_after_it_the_proven_one(self):
        calls = []

        def make(proven):
            self.assertIs(PROVEN_RULE, proven)

            def rule(anchor, axis, side_allowance_mm=0.0):
                calls.append((tuple(anchor), tuple(axis)))
                return 17.0
            rule.advance_rule = "test"
            return rule

        self.assertIs(PROVEN_RULE, current.base_advance_needed_mm)
        with current._advance_rule(make):
            self.assertEqual(17.0, current.base_advance_needed_mm(ANCHOR, AXIS))
            self.assertEqual(17.0, current.Cycle._approach_state({}, ANCHOR, AXIS)["need_mm"], "the capability's approach state reads the rule by name")
            self.assertEqual(17.0, current._CONTROLLER_VIEW.base_advance_needed_mm(ANCHOR, AXIS), "a policy reads it through the controller view")
        self.assertIs(PROVEN_RULE, current.base_advance_needed_mm)
        self.assertEqual(3, len(calls))
        self.assertEqual(PROVEN_RULE(ANCHOR, AXIS), current.Cycle._approach_state({}, ANCHOR, AXIS)["need_mm"])

    def test_an_error_inside_the_window_restores_the_proven_rule(self):
        def make(proven):
            def rule(anchor, axis, side_allowance_mm=0.0):
                return None
            rule.advance_rule = "test"
            return rule

        with self.assertRaises(KeyError), current._advance_rule(make):
            raise KeyError("an advance that ended by an error")
        self.assertIs(PROVEN_RULE, current.base_advance_needed_mm)

    def test_a_window_never_opens_inside_another(self):
        def make(proven):
            def rule(anchor, axis, side_allowance_mm=0.0):
                return 0.0
            rule.advance_rule = "test"
            return rule

        with current._advance_rule(make):
            with self.assertRaises(RuntimeError), current._advance_rule(make):
                pass
            self.assertTrue(getattr(current.base_advance_needed_mm, "advance_rule", None))
        self.assertIs(PROVEN_RULE, current.base_advance_needed_mm)

    def test_every_controller_name_the_executor_reads_is_defined_by_the_controller(self):
        """The executor and the policies read the controller through ctl, by name: a name the controller does not define
        would fail only on the path that reads it, so every one is checked here."""
        import ast
        for name in ("placement_executor", "reach_carry", "policy_arm_reach", "policy_advance_then_reach", "placement_selector", "placement_policy", "reach_model"):
            with self.subTest(name):
                tree = ast.parse((ROOT / "autonomy" / "manipulation" / f"{name}.py").read_text())
                read = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ctl"}
                self.assertEqual(set(), {attr for attr in read if not hasattr(current, attr)})

    def test_the_executor_never_imports_the_controller(self):
        for name in ("placement_executor", "reach_carry", "policy_arm_reach", "policy_advance_then_reach"):
            with self.subTest(name):
                source = (ROOT / "autonomy" / "manipulation" / f"{name}.py").read_text()
                self.assertNotIn("import run_single_cube_cycle", source)
                self.assertNotIn("from run_single_cube_cycle", source)


class Loop:
    """What the frozen advance loop (Cycle._advance_base_held, called unchanged) reads of its Cycle, scripted: every pulse
    moves the base the next distance of `realized` straight along the axis, and the pad measures exactly that. Every
    refusal's code and values are kept (codes, values)."""

    target = None
    events = current._NO_EVENTS
    _approach_state = staticmethod(current.Cycle._approach_state)

    def __init__(self, realized):
        self.realized, self.pulses, self.codes, self.values = list(realized), [], [], []

    def run_pose(self, *_args, **_kwargs):
        return {"perception": []}

    def _pad_frame(self, _view):
        return {"homography_pad_to_image": None, "homography_image_to_pad": None}

    def state(self, name, **fields):
        pass

    def failed(self, code, message, **values):
        self.codes.append(code)
        self.values.append(values)
        return CycleFailed(f"{code}: {message}")

    def measured_base_pulse(self, vx_mps, duration_s, holding, anchor, axis, frame):
        step = self.realized.pop(0)
        self.pulses.append(step)
        self._pulse_view = {"perception": []}
        return {"anchor_pad_m": [anchor[0] + AXIS[0] * step / 1000.0, anchor[1] + AXIS[1] * step / 1000.0], "shift_pad_mm": [AXIS[0] * step, AXIS[1] * step],
                "advance_mm": step, "lateral_mm": 0.0, "distortion_mm": 0.0}, frame


class TheCumulativeBudget(unittest.TestCase):
    """The proven continuation of THE IN-RUN FALLBACK (placement_executor._proven_continuation): the frozen loop pulses on
    the proven rule held to the budget left (placement_executor._cumulative_rule), after an advance_then_reach advance of
    `prior` mm. The anchor lies where the proven rule needs `need_mm` of advance from it."""

    def advance(self, prior: float, need_mm: float, realized, window: bool = True):
        edge = current.PAD_SIZE_M[1] * 500.0
        anchor = (0.0, (-edge + current.PLACE_REQUIRED_CENTRE_MM + current.BASE_TARGET_BUFFER_MM + current.VISUAL_ANCHOR_UNCERTAINTY_MM - need_mm) / 1000.0)
        self.assertAlmostEqual(need_mm, PROVEN_RULE(anchor, AXIS), delta=1.0)
        held = rc.Held(view={}, grasp_out_mm=0.0, grasp_pan_deg=0.0, anchor=anchor, direction=AXIS, axis=AXIS, advanced_mm=prior, rate=current.BASE_RATE_PRIOR_MM_S,
                       out_cap_mm=None, seen=None)
        loop = Loop(realized)
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "scan-home.json"
            home.write_text(json.dumps({"measured_joints_deg": {"shoulder_pan": 0.0, "shoulder_lift": -80.0, "elbow_flex": 40.0, "wrist_flex": 65.0, "wrist_roll": 0.0}}))
            with mock.patch.object(current, "HOME_FILE", home), mock.patch.object(current.cli, "pregrasp_return_poses", lambda _home: [None]):
                try:
                    if window:
                        with current._advance_rule(lambda proven: pe._cumulative_rule(loop, current._CONTROLLER_VIEW, held, proven)):
                            returned = current.Cycle._advance_base_held(loop, anchor, AXIS)
                    else:
                        returned = current.Cycle._advance_base_held(loop, anchor, AXIS)
                except CycleFailed:
                    returned = None
        self.assertIs(PROVEN_RULE, current.base_advance_needed_mm)
        return loop, returned

    def test_the_proven_loop_alone_refuses_a_last_pulse_past_the_budget(self):
        loop, returned = self.advance(0.0, 110.0, [40.0, 40.0, 20.0, 30.0], window=False)
        self.assertEqual((None, ["base_advanced_past_bound"], 4), (returned, loop.codes, len(loop.pulses)))

    def test_a_last_pulse_that_carries_the_runs_advance_past_the_budget_is_refused_and_nothing_further_is_commanded(self):
        loop, returned = self.advance(60.0, 55.0, [20.0, 20.0, 5.0, 30.0])
        self.assertIsNone(returned, "the advance does not return: the placement after it is never reached")
        self.assertEqual((["reach_base_advanced_past_bound"], 4), (loop.codes, len(loop.pulses)))
        values = loop.values[-1]
        self.assertAlmostEqual(135.0, values["advanced_mm"], delta=1e-6)
        self.assertEqual((60.0, current.BASE_MAX_TOTAL_ADVANCE_MM), (values["advanced_before_mm"], values["bounded_total_advance_mm"]))
        self.assertAlmostEqual(75.0, values["advanced_since_mm"], delta=1e-6)

    def test_an_overshoot_before_the_last_pulse_is_refused_where_it_happened(self):
        loop, returned = self.advance(60.0, 55.0, [20.0, 45.0, 10.0, 10.0])
        self.assertIsNone(returned)
        self.assertEqual((["reach_base_advanced_past_bound"], 2), (loop.codes, len(loop.pulses)))
        self.assertAlmostEqual(125.0, loop.values[-1]["advanced_mm"], delta=1e-6)

    def test_a_need_the_budget_left_does_not_cover_is_refused_before_any_pulse(self):
        loop, returned = self.advance(60.0, 65.0, [30.0, 30.0])
        self.assertIsNone(returned)
        self.assertEqual((["base_advance_exceeds_bound"], []), (loop.codes, loop.pulses))

    def test_an_advance_inside_the_budget_returns_where_the_loop_stopped(self):
        loop, returned = self.advance(60.0, 55.0, [25.0, 25.0, 5.0])
        self.assertIsNotNone(returned)
        self.assertEqual(([], 2), (loop.codes, len(loop.pulses)), "the need left (5 mm) is inside the loop's tolerance")
        self.assertAlmostEqual(50.0, returned[1], delta=1e-6)


class DriftingLoop(Loop):
    """Loop whose every pulse moves the base the next distance of `realized` at the next angle of `degrees` off the reach
    axis (deg, toward +x in PAD_FRAME), and the pad measures exactly that."""

    def __init__(self, realized, degrees=None):
        super().__init__(realized)
        self.degrees = [0.0] * len(self.realized) if degrees is None else list(degrees)

    def measured_base_pulse(self, vx_mps, duration_s, holding, anchor, axis, frame):
        step, angle = self.realized.pop(0), math.radians(self.degrees.pop(0))
        self.pulses.append(step)
        self._pulse_view = {"perception": []}
        aside, along = step * math.sin(angle), step * math.cos(angle)
        return {"anchor_pad_m": [anchor[0] + aside / 1000.0, anchor[1] + along / 1000.0], "shift_pad_mm": [aside, along],
                "advance_mm": along, "lateral_mm": -aside, "distortion_mm": 0.0}, frame


def the_frozen_advance(loop, anchor, make_rule):
    """The frozen advance loop (Cycle._advance_base_held, called unchanged) on `loop`, from `anchor`, inside the controller's
    rule window lending it make_rule's rule. What it returned, or None when it raised."""
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory) / "scan-home.json"
        home.write_text(json.dumps({"measured_joints_deg": {"shoulder_pan": 0.0, "shoulder_lift": -80.0, "elbow_flex": 40.0, "wrist_flex": 65.0, "wrist_roll": 0.0}}))
        with mock.patch.object(current, "HOME_FILE", home), mock.patch.object(current.cli, "pregrasp_return_poses", lambda _home: [None]):
            try:
                with current._advance_rule(make_rule):
                    return current.Cycle._advance_base_held(loop, anchor, AXIS)
            except CycleFailed:
                return None


class TheFurtherAdvancesBudget(unittest.TestCase):
    """advance_then_reach asked again by THE IN-RUN FALLBACK (verify r9 safety MF1): the frozen loop pulses a second time in
    the same run, on the policy's own rule (policy_advance_then_reach.remaining_need_rule), after `prior` mm already
    advanced. The loop's own checks count its own total alone, so the rule holds the whole run to the 120 mm budget: the
    path the base travelled, charged before a pulse and checked right after it, the last pulse included. On the centreline
    206 mm behind the pad's centre the policy plans a 45 mm advance (for a block the proven policy refuses: the long carry's
    cap); 271 mm behind it, 110 mm."""

    ANCHOR, FAR = (0.0, -0.206), (0.0, -0.271)

    def advance(self, prior: float, realized, degrees=None, anchor=ANCHOR):
        ctl = current._CONTROLLER_VIEW
        held = rc.Held(view={}, grasp_out_mm=20.0, grasp_pan_deg=0.0, anchor=anchor, direction=AXIS, axis=AXIS, advanced_mm=prior, rate=current.BASE_RATE_PRIOR_MM_S,
                       out_cap_mm=None, seen=None, tried=("advance_then_reach",), min_advance_mm=pe._first_advance_mm(ctl), proven_admits=False)
        self.assertEqual({self.ANCHOR: 45.0, self.FAR: 110.0}[anchor], policy_advance_then_reach.plan(ctl, held.but(advanced_mm=0.0).situation(ctl)).base_advance_mm)
        loop = DriftingLoop(realized, degrees)
        returned = the_frozen_advance(loop, anchor, lambda proven: policy_advance_then_reach.remaining_need_rule(loop, ctl, held))
        self.assertIs(PROVEN_RULE, current.base_advance_needed_mm)
        return loop, returned

    def test_a_last_pulse_that_carries_the_runs_advance_past_the_budget_is_refused_and_nothing_further_is_commanded(self):
        loop, returned = self.advance(60.0, [10.0, 10.0, 10.0, 45.0])
        self.assertIsNone(returned, "the advance does not return: the approach, the carry and the release after it are never reached")
        self.assertEqual((["reach_further_advance_past_bound"], 4), (loop.codes, len(loop.pulses)))
        values = loop.values[-1]
        self.assertAlmostEqual(135.0, values["advanced_mm"], delta=1e-6)
        self.assertEqual((60.0, current.BASE_MAX_TOTAL_ADVANCE_MM), (values["advanced_before_mm"], values["bounded_total_advance_mm"]))
        self.assertAlmostEqual(75.0, values["advanced_since_mm"], delta=1e-6)

    def test_an_overshoot_before_the_last_pulse_is_refused_where_it_happened(self):
        loop, returned = self.advance(60.0, [20.0, 45.0, 10.0, 10.0])
        self.assertIsNone(returned)
        self.assertEqual((["reach_further_advance_past_bound"], 2), (loop.codes, len(loop.pulses)))
        self.assertAlmostEqual(125.0, loop.values[-1]["advanced_mm"], delta=1e-6)

    def test_the_base_is_charged_the_path_it_travelled_not_its_progress_along_the_axis(self):
        """24 deg off the axis on every pulse: the path is 62 mm and the progress along the axis 56.6, so the run's advance
        passes the budget only as the frozen loop counts it, along the path."""
        loop, returned = self.advance(60.0, [12.0, 12.0, 12.0, 26.0], [24.0] * 4)
        self.assertIsNone(returned)
        self.assertEqual((["reach_further_advance_past_bound"], 4), (loop.codes, len(loop.pulses)))
        self.assertAlmostEqual(122.0, loop.values[-1]["advanced_mm"], delta=1e-6)

    def test_before_a_pulse_the_need_is_held_to_the_budget_the_path_left(self):
        """24 deg off the axis, 10 mm a pulse, after 74 mm: after two pulses the path is 20 mm and the progress along the axis
        18.3; the need from there fits what the progress left and not what the path left, so the rule answers None and the
        loop refuses before the third pulse, as its own check of its own total and the need does in a run's first advance."""
        loop, returned = self.advance(74.0, [10.0] * 4, [24.0] * 4)
        self.assertIsNone(returned)
        self.assertEqual((["base_advance_exceeds_bound"], 2), (loop.codes, len(loop.pulses)))
        self.assertIsNone(loop.values[-1]["needed_mm"])

    def test_a_need_the_budget_left_does_not_cover_is_refused_before_any_pulse(self):
        loop, returned = self.advance(80.0, [30.0, 30.0])
        self.assertIsNone(returned)
        self.assertEqual((["base_advance_exceeds_bound"], []), (loop.codes, loop.pulses))
        self.assertIsNone(loop.values[-1]["needed_mm"], "45 mm of need, 40 left of the budget: the rule answers None")

    def test_a_further_advance_inside_the_budget_returns_where_the_loop_stopped(self):
        loop, returned = self.advance(60.0, [20.0, 20.0, 5.0])
        self.assertIsNotNone(returned)
        self.assertEqual(([], 2), (loop.codes, len(loop.pulses)))
        self.assertAlmostEqual(40.0, returned[1], delta=1e-6)

    def test_a_further_advance_that_ends_on_the_budget_returns(self):
        """The budget is the frozen loop's own bound: a run's advance of exactly 120 mm is inside it."""
        loop, returned = self.advance(60.0, [10.0, 10.0, 10.0, 30.0])
        self.assertIsNotNone(returned)
        self.assertEqual(([], 4), (loop.codes, len(loop.pulses)))
        self.assertAlmostEqual(60.0, returned[1], delta=1e-6)

    def test_the_runs_first_advance_is_held_to_the_budget_by_the_frozen_loops_own_check(self):
        """Nothing advanced before: the loop's own total is the run's, and its own check after the last pulse ends it with
        its own code, as with the proven rule."""
        loop, returned = self.advance(0.0, [10.0, 10.0, 10.0, 95.0], anchor=self.FAR)
        self.assertIsNone(returned)
        self.assertEqual((["base_advanced_past_bound"], 4), (loop.codes, len(loop.pulses)))
        self.assertAlmostEqual(125.0, loop.values[-1]["advanced_mm"], delta=1e-6)


class TheProvenPolicyGoesOnFromAMovedAnchor(unittest.TestCase):
    """verify r2 replay MUST-FIX 2: after advance_then_reach's measured advance and its refused reach, the proven plan from
    where the advance ended can be the arm alone (the moved anchor lies inside the proven arm-only band). The fallback, whose
    next plan must move the base, never took it, and the run ended held although the proven rule places the block by its
    base branch. The proven candidate is then that base branch: the proven rule's base advance from here and a release at
    about the grasp radius, what the proven stage itself does after its own arm-only carry fell short."""

    MOVED = (0.0113, -0.1501)  # row 8 of the replay (cycle-run-20260920T103136Z's start) after 18 mm of measured advance along +y
    POLICIES = (policy_arm_reach, policy_advance_then_reach)

    def held(self):
        return rc.Held(view={}, grasp_out_mm=19.5, grasp_pan_deg=0.0, anchor=self.MOVED, direction=AXIS, axis=AXIS, advanced_mm=18.0, rate=current.BASE_RATE_PRIOR_MM_S,
                       out_cap_mm=93.8, seen=None, tried=("advance_then_reach",))

    def test_the_proven_plan_from_there_is_the_arm_alone(self):
        proven = placement_selector.proven_plan(current._CONTROLLER_VIEW, current.placement_plan, self.held().situation(current._CONTROLLER_VIEW))
        self.assertEqual((True, pp.MODE_ARM_ONLY, 0.0), (proven.admitted, proven.mode, proven.base_advance_mm))

    def test_a_run_that_may_drive_the_base_goes_on_with_the_proven_rules_base_branch(self):
        """Once advance_then_reach was asked again and handed the block back again (TheAdvanceMemberIsAskedAgain), the proven
        policy is what the fallback goes on with: its base branch from the moved anchor."""
        need = PROVEN_RULE(self.MOVED, AXIS, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM)
        self.assertTrue(current.BASE_ADVANCE_TOLERANCE_MM < need <= current.BASE_MAX_TOTAL_ADVANCE_MM - 18.0, need)
        held = self.held().but(tried=("advance_then_reach", "advance_then_reach"))
        for target, run_mode in ((Target(pp.MODE_BASE_ADVANCE, True, True), pp.MODE_BASE_ADVANCE), (Target("auto", True, True), pp.MODE_ARM_ONLY)):
            with self.subTest(run_mode=run_mode):
                plan, candidates = pe._next(Ctx(target), current._CONTROLLER_VIEW, held, self.POLICIES, run_mode, "advance_then_reach")
                self.assertIsNotNone(plan, [candidate.record() for candidate in candidates])
                self.assertEqual((pp.PROVEN, pp.MODE_BASE_ADVANCE, 0.0), (plan.policy, plan.mode, plan.fk_travel_mm))
                self.assertAlmostEqual(need, plan.base_advance_mm, delta=1e-9)
                self.assertIn("base", plan.sentence)
                self.assertEqual({"mode": pp.MODE_BASE_ADVANCE, "fk_travel_mm": 0.0, "base_advance_mm": need}, plan.raw)

    def test_a_base_branch_the_proven_rule_refuses_says_so_and_is_not_taken(self):
        """By the side edge the proven arm alone admits the block and its base rule does not (the side edge binds): the base
        branch is refused, and its sentence names the base rule, never the arm alone."""
        side = (0.150, -0.150)
        self.assertIsNone(PROVEN_RULE(side, AXIS, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM))
        held = rc.Held(view={}, grasp_out_mm=19.5, grasp_pan_deg=0.0, anchor=side, direction=AXIS, axis=AXIS, advanced_mm=18.0, rate=current.BASE_RATE_PRIOR_MM_S,
                       out_cap_mm=93.8, seen=None, tried=("advance_then_reach",))
        self.assertEqual(pp.MODE_ARM_ONLY, placement_selector.proven_plan(current._CONTROLLER_VIEW, current.placement_plan, held.situation(current._CONTROLLER_VIEW)).mode)
        plan, candidates = pe._next(Ctx(Target(pp.MODE_BASE_ADVANCE, True, True)), current._CONTROLLER_VIEW, held, self.POLICIES, pp.MODE_BASE_ADVANCE, "advance_then_reach")
        (candidate,) = [plan for plan in candidates if plan.policy == pp.PROVEN]
        self.assertFalse(candidate.admitted)
        self.assertIn("base rule", candidate.sentence)
        self.assertNotIn("arm alone", candidate.sentence)
        self.assertTrue(plan is None or plan.policy != pp.PROVEN)

    def test_every_proven_base_branch_sentence_is_in_the_records_text_shape(self):
        import placement_envelope
        for anchor in (self.MOVED, (0.150, -0.150)):
            held = rc.Held(view={}, grasp_out_mm=19.5, grasp_pan_deg=0.0, anchor=anchor, direction=AXIS, axis=AXIS, advanced_mm=18.0, rate=current.BASE_RATE_PRIOR_MM_S,
                           out_cap_mm=93.8, seen=None, tried=("advance_then_reach",))
            (candidate,) = [plan for plan in pe._next(Ctx(Target(pp.MODE_BASE_ADVANCE, True, True)), current._CONTROLLER_VIEW, held, self.POLICIES, pp.MODE_BASE_ADVANCE,
                                                      "advance_then_reach")[1] if plan.policy == pp.PROVEN]
            with self.subTest(anchor=anchor):
                self.assertTrue(placement_envelope.TEXT_SHAPE.fullmatch(candidate.sentence), candidate.sentence)

    def test_a_run_that_may_not_drive_the_base_has_nothing_left(self):
        plan, candidates = pe._next(Ctx(Target(pp.MODE_ARM_ONLY, False, False)), current._CONTROLLER_VIEW, self.held(), self.POLICIES, pp.MODE_ARM_ONLY, "advance_then_reach")
        self.assertIsNone(plan)
        self.assertIn(pp.MODE_BASE_ADVANCE, [candidate.mode for candidate in candidates if candidate.policy == pp.PROVEN])

    def test_a_proven_plan_that_moves_the_base_goes_on_unchanged(self):
        far = (0.0145, -0.1825)
        held = rc.Held(view={}, grasp_out_mm=19.5, grasp_pan_deg=0.0, anchor=far, direction=AXIS, axis=AXIS, advanced_mm=0.0, rate=current.BASE_RATE_PRIOR_MM_S,
                       out_cap_mm=93.8, seen=None, tried=("arm_reach",))
        situation = held.situation(current._CONTROLLER_VIEW)
        proven = placement_selector.proven_plan(current._CONTROLLER_VIEW, current.placement_plan, situation)
        self.assertEqual(pp.MODE_BASE_ADVANCE, proven.mode)
        candidate = [plan for plan in pe._next(Ctx(Target("auto", True, True)), current._CONTROLLER_VIEW, held, self.POLICIES, pp.MODE_ARM_ONLY, "advance_then_reach")[1]
                     if plan.policy == pp.PROVEN][0]
        self.assertEqual((proven.raw, proven.sentence), (candidate.raw, candidate.sentence))


class TheAdvanceMemberIsAskedAgain(unittest.TestCase):
    """verify r7 policy MF2. After advance_then_reach handed the block back, THE IN-RUN FALLBACK offered only the policies
    after it, so the proven policy's larger advance, or a refusal saying that no policy of the run places the block by
    moving the base, while advance_then_reach's own re-plan with a pulsed advance placed it inside the budget left. Now the
    fallback asks the advance member again, once, before the proven policy, and the exhaustion names what it asked."""

    LIVE, LIVE_AXIS = (-0.035768012099579305, -0.2129612198129184), (0.0616, 0.9981)
    POLICIES = (policy_arm_reach, policy_advance_then_reach)

    def held(self, tried=("advance_then_reach",), advanced=52.8):
        """The operator's block of 2026-09-30 after advance_then_reach's measured advance and its refused release, the way
        back come back (the committed scene's held state, verify r7 policy MF2's probe): 67.2 mm of the budget left."""
        norm = (self.LIVE_AXIS[0] ** 2 + self.LIVE_AXIS[1] ** 2) ** 0.5
        axis = (self.LIVE_AXIS[0] / norm, self.LIVE_AXIS[1] / norm)
        anchor = (self.LIVE[0] + axis[0] * advanced / 1000.0, self.LIVE[1] + axis[1] * advanced / 1000.0)
        return rc.Held(view={"tool": {"out_mm": 30.0}}, grasp_out_mm=21.1, grasp_pan_deg=0.0, anchor=anchor, direction=axis, axis=axis, advanced_mm=advanced,
                       rate=current.BASE_RATE_PRIOR_MM_S, out_cap_mm=117.0, seen=None, tried=tried, min_advance_mm=pe._first_advance_mm(current._CONTROLLER_VIEW),
                       proven_admits=False)

    def test_after_advance_then_reach_handed_it_back_its_own_pulsed_re_plan_is_asked_before_the_proven_policy(self):
        plan, candidates = pe._next(Ctx(Target("auto", True, True)), current._CONTROLLER_VIEW, self.held(), self.POLICIES, pp.MODE_BASE_ADVANCE, "advance_then_reach")
        self.assertEqual(["advance_then_reach", pp.PROVEN], [candidate.policy for candidate in candidates])
        proven = candidates[-1]
        self.assertGreater(proven.base_advance_mm, current.BASE_MAX_TOTAL_ADVANCE_MM - 52.8, "the proven rule needs more than the budget left")
        self.assertIsNotNone(plan, [candidate.record() for candidate in candidates])
        self.assertEqual(("advance_then_reach", pp.MODE_BASE_ADVANCE), (plan.policy, plan.mode))
        self.assertTrue(pe._first_advance_mm(current._CONTROLLER_VIEW) <= plan.base_advance_mm <= current.BASE_MAX_TOTAL_ADVANCE_MM - 52.8)
        self.assertLessEqual(plan.arm_out_mm, 117.0, "never past the out the loaded arm reached")

    def test_it_is_asked_again_once_and_the_chain_stays_bounded(self):
        self.assertEqual(["advance_then_reach"], [module.WORD for module in pe.asked(self.POLICIES, "advance_then_reach", ("advance_then_reach",))])
        self.assertEqual(["advance_then_reach"], [module.WORD for module in pe.asked(self.POLICIES, "advance_then_reach", ("arm_reach", "advance_then_reach"))])
        self.assertEqual([], pe.asked(self.POLICIES, "advance_then_reach", ("advance_then_reach", "advance_then_reach")))
        self.assertEqual(["advance_then_reach"], [module.WORD for module in pe.asked(self.POLICIES, "arm_reach", ("arm_reach",))])
        self.assertEqual([], pe.asked((policy_arm_reach,), "arm_reach", ("arm_reach",)))
        _plan, candidates = pe._next(Ctx(Target("auto", True, True)), current._CONTROLLER_VIEW, self.held(("advance_then_reach", "advance_then_reach")), self.POLICIES,
                                     pp.MODE_BASE_ADVANCE, "advance_then_reach")
        self.assertEqual([pp.PROVEN], [candidate.policy for candidate in candidates])

    def test_with_advance_then_reach_named_alone_its_own_advancing_plan_follows_its_refused_arm_alone(self):
        """advance_then_reach alone, its plan the arm alone (no advance), its release refused from the stance it was held at:
        the fallback asks its own plan with the first pulsed advance, where it used to go on with the proven policy only. At
        SEARCH the capability gate asks what the fallback would go on with, the same way."""
        anchor, axis = (0.0145, -0.158), (0.0, 1.0)
        held = rc.Held(view={"tool": {"out_mm": 30.0}}, grasp_out_mm=36.2, grasp_pan_deg=0.0, anchor=anchor, direction=axis, axis=axis, advanced_mm=0.0,
                       rate=current.BASE_RATE_PRIOR_MM_S, out_cap_mm=None, seen=None, tried=("advance_then_reach",),
                       min_advance_mm=pe._first_advance_mm(current._CONTROLLER_VIEW), proven_admits=False)
        plan, candidates = pe._next(Ctx(Target("auto", True, True)), current._CONTROLLER_VIEW, held, (policy_advance_then_reach,), pp.MODE_ARM_ONLY, "advance_then_reach")
        self.assertEqual(["advance_then_reach", pp.PROVEN], [candidate.policy for candidate in candidates])
        self.assertEqual(("advance_then_reach", pe._first_advance_mm(current._CONTROLLER_VIEW)), (plan.policy, plan.base_advance_mm))
        search = pp.Situation(anchor, axis, 14.2, False, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM, proven_admits=False)
        self.assertEqual("advance_then_reach", pe.fallback_at_search(current._CONTROLLER_VIEW, search, (policy_advance_then_reach,), "advance_then_reach").policy)

    def test_the_exhaustion_names_the_policies_it_asked_and_the_budget_left(self):
        class Failing(Ctx):
            def failed(self, code, message, **values):
                self.ended = (code, message, values)
                return CycleFailed(message)

        held = self.held(("advance_then_reach", "advance_then_reach"), advanced=60.0)
        candidates = pe._candidates(current._CONTROLLER_VIEW, held.situation(current._CONTROLLER_VIEW), self.POLICIES, "advance_then_reach", held.tried)
        ctx = Failing(Target("auto", True, True))
        with self.assertRaises(CycleFailed):
            pe._exhausted(ctx, current._CONTROLLER_VIEW, held, candidates, rc.Attempt(None, held, "the release admission refused: the hold was not steady", rc.HOLD_NOT_STEADY))
        code, message, values = ctx.ended
        self.assertEqual(("reach_policies_exhausted_while_held", "proven", 60.0), (code, values["policies_asked"], values["advance_budget_left_mm"]))
        self.assertIn("none of the placement policies asked from here (proven)", message)
        self.assertIn("within the 60 mm left of the advance budget", message)
        self.assertNotIn("no placement policy of this run", message)


class TheHeldRefusalOfAMissionRunThatMayNotDrive(unittest.TestCase):
    """The mission's own refusal when the arm alone did not place the block and the policies left place it only by moving
    the base, in a run that carries no capability: an auto target whose SEARCH saw no fallback that moves the base, so it
    was never asked for the capability (a target that declares its mode never meets these words; the World pins the LIVE
    run's refusal, test_reach_policies_execution.TheRefusalWithoutTheCapabilitySaysWhatHappened)."""

    class Failing(Ctx):
        def failed(self, code, message, **values):
            self.ended = (code, message, values)
            return CycleFailed(message)

    def test_it_says_what_refused_the_arm_alone_and_never_that_the_reach_fell_short_when_it_did_not(self):
        held = rc.Held(view={"tool": {"out_mm": 30.0}}, grasp_out_mm=19.5, grasp_pan_deg=0.0, anchor=ANCHOR, direction=AXIS, axis=AXIS, advanced_mm=0.0,
                       rate=current.BASE_RATE_PRIOR_MM_S, out_cap_mm=130.0, seen=None, tried=("arm_reach",))
        moving = pp.Plan("advance_then_reach", True, pp.MODE_BASE_ADVANCE, 20.0, 60.0, 90.0, 40.0, 40.0, "a plan")
        for refusal, why in ((rc.HOLD_NOT_STEADY, "the release admission refused: the hold was not steady"), (rc.CARRY_SHORT, "the carry stopped (low) short of the release admission")):
            with self.subTest(refusal=refusal):
                ctx = self.Failing(Target("auto", True, False))
                with self.assertRaises(CycleFailed):
                    pe._exhausted(ctx, current._CONTROLLER_VIEW, held, [moving], rc.Attempt(None, held, why, refusal))
                code, message, values = ctx.ended
                self.assertEqual(("reach_arm_only_insufficient_while_held", refusal, "arm_reach"), (code, values["refused_by"], values["policies_tried"]))
                self.assertIn(f"the arm alone did not place the block ({why})", message)
                self.assertIn("which this run carries no capability to move", message)
                self.assertNotIn("falls short", message)


class TheProvenTieAtSearch(unittest.TestCase):
    """verify r4 policy MF1: which additive policy the executor may hand a held cube to when SEARCH chose the proven
    arm-alone plan (placement_executor.handed_at_search). The proven arm-alone band only shrinks as the grasp moves out,
    so a proven plan that still places the block with the arm alone at SEARCH's planning grasp (the coarse grasp plus the
    worst recorded under-read) keeps the cube for every grasp within that charge, and nothing is handed; past it, the first
    additive policy the run names is the one the least-motion choice once held takes."""

    def situation(self, pad_y: float) -> pp.Situation:
        return pp.Situation((0.0145, pad_y), AXIS, 0.0, False, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM)

    def test_a_proven_plan_that_holds_at_the_planning_grasp_keeps_the_cube(self):
        for pad_y in (-0.1400, -0.1480):
            with self.subTest(pad_y=pad_y):
                self.assertEqual(pp.MODE_ARM_ONLY, current.placement_plan((0.0145, pad_y), AXIS, 22.0, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM)["mode"])
                self.assertIsNone(pe.handed_at_search(current._CONTROLLER_VIEW, self.situation(pad_y), (policy_arm_reach, policy_advance_then_reach), current.placement_plan))

    def test_a_block_whose_plans_carry_the_proven_loaded_reach_cap_is_never_handed_to_an_additive_policy(self):
        """THE LOADED REACH CAP (placement_policy): the proven policy chose the block at SEARCH, so its plans carry the proven
        loaded reach, and no additive arm-alone plan of it reaches past the proven one; nothing is handed, whatever the
        grasp. A cap that lay farther (a measured loaded-sustain record) would hand it again."""
        pad_y = -0.1525
        for policies in ((policy_arm_reach, policy_advance_then_reach), (policy_advance_then_reach,)):
            with self.subTest(policies=[module.WORD for module in policies]):
                capped = pp.Situation((0.0145, pad_y), AXIS, 0.0, False, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM, proven_admits=True)
                self.assertIsNone(pe.handed_at_search(current._CONTROLLER_VIEW, capped, policies, current.placement_plan))
                with mock.patch.object(pp, "loaded_reach_cap_mm", lambda ctl, situation, proven_admits: current.ARM_LOADED_REACH_OUT_MM + 20.0):
                    self.assertEqual(policies[0].WORD, pe.handed_at_search(current._CONTROLLER_VIEW, capped, policies, current.placement_plan))

    def test_whether_the_proven_policy_placed_the_block_at_search_is_read_from_its_decision(self):
        class Portfolio:
            def __init__(self, admits):
                situation = pp.Situation((0.0145, -0.1525), AXIS, 0.0, False, proven_admits=admits)
                self.last = placement_selector.Decision((), None, None, situation)

        class Run(Ctx):
            def __init__(self, portfolio):
                super().__init__()
                self._search_portfolio = portfolio

        self.assertTrue(pe._proven_admitted_at_search(Run(Portfolio(True))))
        self.assertFalse(pe._proven_admitted_at_search(Run(Portfolio(False))))
        self.assertTrue(pe._proven_admitted_at_search(Run(None)), "with no SEARCH decision to read, the cap never lapses")

    def test_past_it_the_first_additive_policy_the_run_names_may_be_handed_the_cube(self):
        pad_y = -0.1525
        self.assertEqual(pp.MODE_BASE_ADVANCE, current.placement_plan((0.0145, pad_y), AXIS, 22.0, current.PLACE_SIDE_DRIFT_ALLOWANCE_MM)["mode"])
        for policies, handed in (((policy_arm_reach, policy_advance_then_reach), "arm_reach"), ((policy_advance_then_reach,), "advance_then_reach"), ((), None)):
            with self.subTest(policies=[module.WORD for module in policies]):
                self.assertEqual(handed, pe.handed_at_search(current._CONTROLLER_VIEW, self.situation(pad_y), policies, current.placement_plan))


if __name__ == "__main__":
    unittest.main()
