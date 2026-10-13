# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""policy_return alone (track R of docs/autonomous-demo/overnight-plan-2026-09-26.md): the direct 2D error of the return,
events only with no word, and the return_2d word's return along the start axis with both axes gated.

A MovingBase world (a HarnessCycle of autonomy/tests/stage_harness.py) holds where the base is on the pad: its offset from
where SEARCH saw it, and its yaw. Every view is a pad frame of that pose; a base pulse moves the base along ITS forward
axis at a scripted rate, with a scripted sideways drift per pulse. The pulse, the re-observation and the measurement are
the controller's own Cycle.measured_base_pulse, and the proven return is the controller's own Cycle.return_base; the
world replaces Cycle.base_pulse (the gated wrapper, its gates included) with a move that checks the pulse's bounds.
"""
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autonomy" / "tests"))
sys.path.insert(0, str(ROOT / "autonomy" / "manipulation"))

import stage_harness as harness  # noqa: E402
import policy_return  # noqa: E402
import stage_policy  # noqa: E402
from policy_search import SearchOutcome  # noqa: E402

cycle = harness.cycle
CONTACT_PX = (553.0, 545.0)
BASE_H0 = np.array(harness.pad_frame()["homography_image_to_pad"])  # the SEARCH view's image_to_pad: pixel (553, 545) sees (0.0145, -0.1825), image-up is pad +y


def frame_at(x_mm: float, y_mm: float, yaw_deg: float) -> dict:
    """The pad frame of the low-pose view with the base moved by (x, y) mm on the pad and turned by yaw about the SEARCH
    view's contact point: a robot-fixed pixel sees the pad point the robot carried there."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    pivot = BASE_H0 @ np.array([CONTACT_PX[0], CONTACT_PX[1], 1.0])
    pivot = pivot[:2] / pivot[2]
    rotate = np.array([[c, -s, pivot[0] - c * pivot[0] + s * pivot[1] + x_mm / 1000.0], [s, c, pivot[1] - s * pivot[0] - c * pivot[1] + y_mm / 1000.0], [0.0, 0.0, 1.0]])
    image_to_pad = rotate @ BASE_H0
    return {"pad_identity": True, "pad_frame_valid": True, "homography_image_to_pad": image_to_pad.tolist(), "homography_pad_to_image": np.linalg.inv(image_to_pad).tolist()}


class MovingBase(harness.HarnessCycle):
    """A base on the pad. forward_deg: its forward axis, from pad +y toward pad -x (counter-clockwise); rate: realized mm/s
    at the commanded speed; drift: sideways mm (to the left of the motion) added per pulse, a list consumed in order."""

    def __init__(self, *, x=0.0, y=0.0, yaw=0.0, forward_deg=0.0, rate=50.0, drift=(), stop_after_pulses=None, pose=None):
        super().__init__(pose=pose)
        self.x, self.y, self.yaw, self.forward_deg, self.rate = x, y, yaw, forward_deg, rate
        self.drift, self.pulses, self.stop_after_pulses, self.observed = list(drift), [], stop_after_pulses, 0
        self.view_pose = None  # the joints the next views report (default: the current ones)

    def view(self, record_id: str) -> dict:
        self.observed += 1
        pad = frame_at(self.x, self.y, self.yaw)
        record = harness.cube_record(bbox_xyxy=None, pad_frame=pad, observation_id=f"obs-world-{self.observed:04d}")
        return harness.view(self.view_pose or self.current, [record])

    def observe(self, holding: bool) -> dict:
        self.calls.append(("observe", "observe"))
        return self.view("observe")

    def base_pulse(self, vx_mps: float, duration_s: float, holding: bool) -> None:
        """The world's gateway primitive: the bounds the proven pulse checks, then the move."""
        self.calls.append(("base_pulse", f"{vx_mps:+.3f}"))
        assert abs(vx_mps) == cycle.BASE_VX_MPS and cycle.BASE_PULSE_S[0] <= duration_s <= cycle.BASE_PULSE_S[1], (vx_mps, duration_s)
        if self.abort["set"]:
            raise self.failed("operator_abort_before_base_pulse", "operator abort: no further base pulse is commanded", base_ahead_mm=self.base_ahead_mm)
        along = math.copysign(self.rate * duration_s, vx_mps)
        aside = self.drift.pop(0) if self.drift else 0.0
        f = (-math.sin(math.radians(self.forward_deg)), math.cos(math.radians(self.forward_deg)))
        left = (-f[1], f[0])
        self.x += along * f[0] + aside * left[0]
        self.y += along * f[1] + aside * left[1]
        self.pulses.append((vx_mps, duration_s))
        if self.stop_after_pulses is not None and len(self.pulses) >= self.stop_after_pulses:
            self.abort["set"] = True

    def return_base(self, advanced_mm, reference_pad_m, direction, rate):  # the controller's own, on this world
        self.calls.append(("return_base", "return_base"))
        return cycle.Cycle.return_base(self, advanced_mm, reference_pad_m, direction, rate)


def search_view() -> SearchOutcome:
    """SEARCH at the start: the cube's contact pixel at (553, 545), its PAD_FRAME centre where that pixel sees."""
    pad = frame_at(0.0, 0.0, 0.0)
    anchor = [float(v) for v in cycle._through(pad["homography_image_to_pad"], CONTACT_PX)]
    seen = harness.cube_record(bbox_xyxy=(500, 480, 600, 580), contact_px=CONTACT_PX, cube_center_pad_m=anchor, pad_frame=pad, observation_id="obs-search-0001")
    return SearchOutcome(measured=dict(harness.LOW_POSE), seen=seen, template=seen["cube_by_template"], search_frame=pad, start_inside=-20.0, coarse={})


def placed_summary() -> dict:
    return {"object_secured": True, "placed": True, "clearance_target_met": True, "footprint_clearance_mm": 40.0, "base_advance_mm": 60.0, "base_returned": True}


def advanced_world(**world) -> tuple:
    """A world whose base has advanced: returns (ctx, the placement outcome fields run_return is handed)."""
    ctx = MovingBase(**world)
    anchor = [float(v) for v in cycle._through(frame_at(0.0, 0.0, 0.0)["homography_image_to_pad"], CONTACT_PX)]
    moved_anchor = [anchor[0] + ctx.x / 1000.0, anchor[1] + ctx.y / 1000.0]
    f = (-math.sin(math.radians(ctx.forward_deg)), math.cos(math.radians(ctx.forward_deg)))
    ctx.base_ahead_mm = math.hypot(ctx.x, ctx.y)
    fields = {"advanced": math.hypot(ctx.x, ctx.y), "anchor": moved_anchor, "axis": f, "rate": 50.0, "final": {"perception": []}, "worst": 40.0, "margin": 45.0, "placed": {"inside_pad_outline": True}}
    return ctx, fields


def ctl(*words):
    return harness.controller_view(PLACEMENT_ENVELOPE=tuple(words))


def predicates(ctx, name):
    return ctx.events.predicates(name)


def failure_values(ctx) -> dict:
    return [args for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1][2]


class TheWordIsDeclaredAndNotQualified(unittest.TestCase):
    def test_return_2d_is_this_stages_word_and_off_by_default(self):
        self.assertEqual(policy_return.WORDS, ("return_2d",))
        self.assertEqual(policy_return.QUALIFIED, frozenset())
        self.assertIn("return_2d", cycle.envelope_words_declared())
        self.assertNotIn("return_2d", cycle.envelope_words_qualified())
        self.assertEqual(cycle.placement_envelope_words({cycle.PLACEMENT_ENVELOPE_ENV: "return_2d"}), ("return_2d",))
        self.assertIsNone(stage_policy.envelope_refusal(("return_2d",), cycle._STAGE_POLICIES, cycle.PLACEMENT_ENVELOPE_WORDS))

    def test_the_rule_names_the_selection(self):
        for words, selection in (((), "proven"), (("return_2d",), "return_2d")):
            ctx, fields = advanced_world(y=0.0)
            fields["advanced"] = 0.0
            policy_return.run_return(ctx, ctl(*words), placed_summary(), **fields)
            identity = [args[0] for kind, args, _kwargs in ctx.events.calls if kind == "enter_policy"][0]
            self.assertEqual((identity["rule_ref"], identity["selection"]), (f"return.{selection}", selection))
            self.assertIn("base_return_error_2d", identity["predicates"])


class WithNoWordTheDirectErrorIsEventsOnly(unittest.TestCase):
    def run_both(self, **world):
        """The same world returned twice by the proven return: without and with the SEARCH view handed in."""
        runs = []
        for start in (None, search_view()):
            ctx, fields = advanced_world(**world)
            error = None
            try:
                summary = policy_return.run_return(ctx, ctl(), placed_summary(), start=start, **fields)
            except stage_policy.CycleFailed as failure:
                summary, error = None, failure
            runs.append((ctx, summary, error))
        return runs

    def test_the_decision_states_and_commands_are_the_proven_ones(self):
        (plain, plain_summary, _), (measured, measured_summary, _) = self.run_both(x=-4.0, y=60.0, forward_deg=4.0, drift=(-3.0, -3.0))
        strip = lambda ctx: [{k: v for k, v in entry.items() if k != "utc"} for entry in ctx.transitions]  # noqa: E731
        self.assertEqual(strip(plain), strip(measured), "no state line and no field is added with no word")
        self.assertEqual(plain.calls, measured.calls, "no observe and no pulse is added with no word")
        self.assertEqual(plain.pulses, measured.pulses)
        self.assertEqual(plain_summary, measured_summary)
        self.assertTrue(all(vx < 0 for vx, _ in plain.pulses))
        self.assertEqual(predicates(plain, "base_return_error_2d"), [], "without the SEARCH view nothing of the direct error is stated")
        (entry,) = predicates(measured, "base_return_error_2d")
        values = entry["measurements"]
        self.assertEqual(values["reference"], "search_view")
        self.assertTrue(values["comparable"])
        # The world knows where the base is: the direct error is that, on the start axis (pad +y) and across it (left = -x).
        self.assertAlmostEqual(values["along_start_axis_mm"], measured.y, delta=0.05)
        self.assertAlmostEqual(values["lateral_mm"], -measured.x, delta=0.05)
        self.assertAlmostEqual(values["yaw_deg"], 0.0, delta=0.01)
        self.assertEqual(entry["value"], abs(measured.y) <= 10.0 and abs(measured.x) <= 10.0)
        self.assertEqual(values["end_observation_id"], f"obs-world-{measured.observed:04d}")

    def test_a_lateral_outside_the_tolerance_is_stated_and_changes_no_decision(self):
        (plain, plain_summary, plain_error), (measured, measured_summary, measured_error) = self.run_both(x=12.0, y=60.0)
        self.assertEqual(plain_summary, measured_summary)
        self.assertEqual(type(plain_error), type(measured_error))
        (entry,) = predicates(measured, "base_return_error_2d")
        self.assertFalse(entry["value"], "12 mm to the right fails return_2d's lateral gate")
        self.assertAlmostEqual(entry["measurements"]["lateral_mm"], -12.0, delta=0.05)
        self.assertTrue(predicates(measured, "base_returned")[-1]["value"], "the proven along-axis verdict decides")

    def test_a_yaw_is_measured(self):
        (_plain, _s, _e), (measured, _summary, _error) = self.run_both(y=40.0, yaw=-1.5)
        (entry,) = predicates(measured, "base_return_error_2d")
        self.assertAlmostEqual(entry["measurements"]["yaw_deg"], -1.5, delta=0.01)

    def test_base_not_returned_carries_its_views(self):
        (plain, _s, error), (measured, _summary, measured_error) = self.run_both(y=100.0, rate=12.0)
        self.assertIsInstance(measured_error, stage_policy.CycleFailed)
        self.assertEqual(harness.failure_code(measured, measured_error), "base_not_returned")
        evidence = [args for kind, args, _kwargs in measured.events.calls if kind == "cycle_failed"][-1][3]
        ids = [view.get("observation_id") or view["perception"][-1]["observation_id"] for view in evidence]
        self.assertEqual(ids, ["obs-search-0001", f"obs-world-{measured.observed:04d}"])
        self.assertEqual(set(failure_values(plain)), {"remaining_along_axis_mm", "sideways_mm", "base_return_tolerance_mm"}, "the proven values, unchanged")

    def test_an_unreadable_joint_is_never_comparable(self):
        """A NaN or a missing value of ANY scan joint, in the view now or in the SEARCH pose: not comparable, and the
        predicate's value is False whatever the displacement reads."""
        start = search_view()
        reference = policy_return.start_reference(ctl(), start)
        frame = frame_at(0.0, 2.0, 0.0)
        for joint in cycle.SCAN_JOINTS:
            for side in ("now", "search"):
                for unreadable in ("nan", "missing"):
                    with self.subTest(joint=joint, side=side, unreadable=unreadable):
                        now, then = dict(harness.LOW_POSE), dict(harness.LOW_POSE)
                        pose = now if side == "now" else then
                        if unreadable == "nan":
                            pose[joint] = float("nan")
                        else:
                            del pose[joint]
                        error = policy_return.return_error_2d(ctl(), {**reference, "pose": then}, frame, now)
                        self.assertFalse(error["comparable"])
                        self.assertEqual(error["posture_difference_deg"], float("inf"))
                        self.assertFalse(policy_return.returned_2d(ctl(), error))
        # the same readable joints are comparable: the refusal above is the unreadable joint's alone
        self.assertTrue(policy_return.return_error_2d(ctl(), reference, frame, dict(harness.LOW_POSE))["comparable"])

    def test_an_unreadable_joint_in_the_return_view_states_a_value_of_false(self):
        for joint in cycle.SCAN_JOINTS:
            with self.subTest(joint=joint):
                ctx, fields = advanced_world(y=60.0)
                ctx.view_pose = {**ctx.current, joint: float("nan")}
                summary = policy_return.run_return(ctx, ctl(), placed_summary(), start=search_view(), **fields)
                self.assertTrue(summary["base_returned"], "the proven along-axis verdict decides")
                (entry,) = predicates(ctx, "base_return_error_2d")
                self.assertFalse(entry["measurements"]["comparable"])
                self.assertFalse(entry["value"])

    def test_a_search_outcome_that_cannot_be_read_costs_the_predicate_never_the_run(self):
        malformed = SearchOutcome(measured=dict(harness.LOW_POSE), seen={"cube": "not a record"}, template={"cube_center_pad_m": [0.0, -0.18]},
                                  search_frame=frame_at(0.0, 0.0, 0.0), start_inside=-20.0, coarse={})
        plain, plain_fields = advanced_world(y=60.0)
        plain_summary = policy_return.run_return(plain, ctl(), placed_summary(), **plain_fields)
        ctx, fields = advanced_world(y=60.0)
        summary = policy_return.run_return(ctx, ctl(), placed_summary(), start=malformed, **fields)
        self.assertEqual(summary, plain_summary)
        self.assertEqual(ctx.calls, plain.calls)
        self.assertEqual(predicates(ctx, "base_return_error_2d"), [])

    def test_no_advance_no_return_and_nothing_stated(self):
        ctx, fields = advanced_world()
        fields["advanced"] = 0.0
        summary = policy_return.run_return(ctx, ctl(), placed_summary(), start=search_view(), **fields)
        self.assertTrue(summary["base_returned"])
        self.assertEqual(ctx.calls, [])
        self.assertEqual(predicates(ctx, "base_return_error_2d"), [])


class OnTheRecord(unittest.TestCase):
    """The controller's own events object states both predicates as contract v1 payloads, with the stage's rule."""

    def test_the_predicates_are_valid_contract_payloads_with_the_rule(self):
        import contextlib
        import io

        sys.path.insert(0, str(ROOT / "packages" / "demo-contracts" / "python"))
        import demo_contracts
        from test_cycle_events import Recorder

        for words in ((), ("return_2d",)):
            with self.subTest(words=words):
                recorder = Recorder()
                ctx, fields = advanced_world(x=3.0, y=60.0)
                ctx.events = cycle.CycleEvents(recorder)
                with contextlib.redirect_stdout(io.StringIO()):
                    policy_return.run_return(ctx, ctl(*words), placed_summary(), start=search_view(), **fields)
                payloads = {record["payload"]["predicate"]: record["payload"] for record in recorder.records if record["kind"] == "predicate.evaluated"}
                for name in ("base_returned", "base_return_error_2d"):
                    self.assertEqual([], demo_contracts.validate("event#/$defs/payload_predicate_evaluated", payloads[name]), payloads[name])
                    self.assertEqual(payloads[name]["rule_ref"], "return." + ("-".join(words) or "proven"))
                    self.assertEqual(payloads[name]["rule_digest"], policy_return.POLICY.digest)
                named = {item["name"]: item for item in payloads["base_return_error_2d"]["measurements"]}
                self.assertEqual(named["reference"]["value"], "search_view")
                self.assertEqual(named["lateral_mm"]["unit"], "mm")
                self.assertEqual(ctx.events.hook_errors, 0)


class TheReturnAlongTheStartAxis(unittest.TestCase):
    def run_2d(self, start=True, **world):
        ctx, fields = advanced_world(**world)
        try:
            return ctx, policy_return.run_return(ctx, ctl("return_2d"), placed_summary(), start=search_view() if start else None, **fields), None
        except stage_policy.CycleFailed as failure:
            return ctx, None, failure

    def test_it_returns_along_the_start_axis_and_gates_both_axes(self):
        ctx, summary, error = self.run_2d(x=-6.0, y=90.0, forward_deg=6.0, drift=(-1.0, -1.0, -1.0))
        self.assertIsNone(error)
        self.assertTrue(summary["base_returned"])
        self.assertNotIn("return_base", ctx.labels(), "the proven return is not run under the word")
        self.assertTrue(ctx.pulses and all(vx < 0 for vx, _ in ctx.pulses), "reverse pulses only")
        returned = [entry for entry in ctx.transitions if entry["state"] == "BASE_RETURNED"][-1]
        self.assertTrue(returned["value"])
        self.assertLessEqual(abs(returned["remaining_along_axis_mm"]), 10.0)
        self.assertLessEqual(abs(returned["sideways_mm"]), 10.0)
        self.assertEqual(returned["reference"], "search_view")
        self.assertEqual(len(returned["pulse_lateral_mm"]), len(ctx.pulses), "the lateral drift of every reverse pulse is stated")
        measured = [entry for entry in ctx.transitions if entry["state"] == "BASE_MEASURED"]
        # each pulse is measured against the START axis (pad +y here): its lateral is the drift plus the forward axis' tilt
        for entry, (vx, duration) in zip(measured, ctx.pulses):
            # moving back along a forward axis tilted 6 deg to the left moves the base right; the drift of -1 mm is to the right too
            expected = -(math.cos(math.radians(6.0)) * 1.0 + math.sin(math.radians(6.0)) * 50.0 * duration)
            self.assertAlmostEqual(entry["lateral_mm"], expected, delta=0.1)
        (gate,) = predicates(ctx, "base_returned")
        self.assertTrue(gate["value"])
        self.assertEqual(gate["measurements"]["reverse_pulses"], len(ctx.pulses))

    def test_a_14_9_mm_last_pulse_does_not_tilt_the_return_axis(self):
        """Start 2's shape: the advance ENDED on an axis tilted about 10 deg; the proven return reads its reverse pulses
        against that axis, return_2d against the start axis, where the net error is inside both gates."""
        ctx, fields = advanced_world(x=-2.4, y=80.0)
        fields["axis"] = (0.173, 0.985)
        summary = policy_return.run_return(ctx, ctl("return_2d"), placed_summary(), start=search_view(), **fields)
        self.assertTrue(summary["base_returned"])
        (entry,) = predicates(ctx, "base_return_error_2d")
        self.assertAlmostEqual(entry["measurements"]["lateral_mm"], 2.4, delta=0.05)

    def test_a_lateral_beyond_the_tolerance_is_not_returned_and_is_never_corrected(self):
        ctx, summary, error = self.run_2d(x=12.0, y=80.0)
        self.assertIsInstance(error, stage_policy.CycleFailed)
        self.assertEqual(harness.failure_code(ctx, error), "base_not_returned")
        values = failure_values(ctx)
        self.assertLessEqual(abs(values["along_start_axis_mm"]), 10.0)
        self.assertAlmostEqual(values["lateral_mm"], -12.0, delta=0.05)
        self.assertFalse(values["overshoot_refused"])
        self.assertTrue(all(vx < 0 for vx, _ in ctx.pulses), "no lateral and no forward command exists")
        self.assertFalse(predicates(ctx, "base_returned")[-1]["value"])
        evidence = [args for kind, args, _kwargs in ctx.events.calls if kind == "cycle_failed"][-1][3]
        self.assertEqual(len(evidence), 3, "the SEARCH view, the return's first view and its last view")

    def test_an_overshoot_beyond_the_tolerance_commands_no_forward_pulse(self):
        ctx, summary, error = self.run_2d(y=15.0, rate=150.0)
        self.assertEqual(harness.failure_code(ctx, error), "base_not_returned")
        self.assertEqual(len(ctx.pulses), 1)
        self.assertLess(ctx.pulses[0][0], 0)
        values = failure_values(ctx)
        self.assertTrue(values["overshoot_refused"])
        self.assertLess(values["along_start_axis_mm"], -10.0)

    def test_an_overshoot_within_the_tolerance_is_accepted(self):
        ctx, summary, error = self.run_2d(y=15.0, rate=80.0)
        self.assertIsNone(error)
        self.assertTrue(summary["base_returned"])
        self.assertEqual(len(ctx.pulses), 1)

    def test_the_pulses_are_bounded(self):
        ctx, summary, error = self.run_2d(y=150.0, rate=41.0)
        self.assertLessEqual(len(ctx.pulses), cycle.BASE_MAX_PULSES)
        self.assertEqual(harness.failure_code(ctx, error), "base_not_returned")

    def test_a_stop_between_pulses_ends_after_the_read_only_reobservation(self):
        ctx, summary, error = self.run_2d(y=90.0, stop_after_pulses=1)
        self.assertEqual(harness.failure_code(ctx, error), "operator_abort_base_path")
        self.assertEqual(len(ctx.pulses), 1)
        self.assertEqual(ctx.labels("observe"), ["observe", "observe"], "the return's first view and the pulse's re-observation, nothing after")

    def test_without_the_search_view_the_proven_return_runs_and_says_so(self):
        ctx, summary, error = self.run_2d(start=False, y=60.0)
        self.assertIn("return_base", ctx.labels())
        self.assertIn("BASE_RETURN_2D_UNAVAILABLE", ctx.states())
        self.assertTrue(summary["base_returned"])

    def test_a_view_not_comparable_with_search_runs_the_proven_return(self):
        ctx, fields = advanced_world(y=60.0)
        ctx.view_pose = {**ctx.current, "elbow_flex": ctx.current["elbow_flex"] + 2.0}
        summary = policy_return.run_return(ctx, ctl("return_2d"), placed_summary(), start=search_view(), **fields)
        self.assertIn("return_base", ctx.labels())
        unavailable = [entry for entry in ctx.transitions if entry["state"] == "BASE_RETURN_2D_UNAVAILABLE"][0]
        self.assertAlmostEqual(unavailable["posture_difference_deg"], 2.0, delta=0.01)
        self.assertTrue(summary["base_returned"])

    def test_an_unreadable_joint_in_either_view_is_never_comparable(self):
        """A NaN of ANY scan joint in the return's view, or a NaN or a missing value in the SEARCH pose: the word runs the
        proven return and says so, nothing of the word's own is commanded. (A view whose pose lacks a joint is not a view:
        the controller's read raises before one exists; return_error_2d's own test covers the missing key.)"""
        for joint in cycle.SCAN_JOINTS:
            for side, unreadable in (("return view", "nan"), ("search pose", "nan"), ("search pose", "missing")):
                with self.subTest(joint=joint, side=side, unreadable=unreadable):
                    ctx, fields = advanced_world(y=60.0)
                    start = search_view()
                    pose = dict(ctx.current) if side == "return view" else start.measured
                    if unreadable == "nan":
                        pose[joint] = float("nan")
                    else:
                        del pose[joint]
                    if side == "return view":
                        ctx.view_pose = pose
                    summary = policy_return.run_return(ctx, ctl("return_2d"), placed_summary(), start=start, **fields)
                    self.assertIn("return_base", ctx.labels())
                    self.assertNotIn("BASE_RETURN_2D", ctx.states())
                    unavailable = [entry for entry in ctx.transitions if entry["state"] == "BASE_RETURN_2D_UNAVAILABLE"]
                    self.assertEqual(len(unavailable), 1)
                    self.assertTrue(summary["base_returned"])

    def test_a_search_outcome_that_cannot_be_read_runs_the_proven_return_and_says_so(self):
        malformed = SearchOutcome(measured=dict(harness.LOW_POSE), seen={"cube": "not a record"}, template={"cube_center_pad_m": [0.0, -0.18]},
                                  search_frame=frame_at(0.0, 0.0, 0.0), start_inside=-20.0, coarse={})
        ctx, fields = advanced_world(y=60.0)
        summary = policy_return.run_return(ctx, ctl("return_2d"), placed_summary(), start=malformed, **fields)
        self.assertIn("return_base", ctx.labels())
        self.assertNotIn("BASE_RETURN_2D", ctx.states())
        self.assertIn("BASE_RETURN_2D_UNAVAILABLE", ctx.states())
        self.assertTrue(summary["base_returned"])

    def test_no_pad_frame_at_the_first_view_is_its_own_failure_before_any_pulse(self):
        ctx, fields = advanced_world(y=60.0)
        ctx.observe = lambda holding: harness.view(ctx.current, [harness.cube_record()])
        with self.assertRaises(stage_policy.CycleFailed) as caught:
            policy_return.run_return(ctx, ctl("return_2d"), placed_summary(), start=search_view(), **fields)
        self.assertEqual(harness.failure_code(ctx, caught.exception), "base_return_2d_no_pad_frame")
        self.assertEqual(ctx.pulses, [])

    def test_a_placement_failure_still_outranks_the_return(self):
        ctx, fields = advanced_world(y=60.0)
        summary = {**placed_summary(), "placed": False}
        with self.assertRaises(stage_policy.CycleFailed) as caught:
            policy_return.run_return(ctx, ctl("return_2d"), summary, start=search_view(), **fields)
        self.assertEqual(harness.failure_code(ctx, caught.exception), "placed_not_demonstrated")
        self.assertTrue(summary["base_returned"], "the base was still returned first")


if __name__ == "__main__":
    unittest.main()
