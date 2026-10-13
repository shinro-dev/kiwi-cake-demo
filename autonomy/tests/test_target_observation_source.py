#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_target_observation_source.py: autonomy/motion/target_observation_source.py, the ACTUAL observation of
# the selected target in the mobile_approach capability's contract v2, and the refusal that names every reason there is none.
#   - resolve() returns an ObservedTarget for a complete executor state (the approach state, the stamped stationary capture,
#     the selection, the perception budget), with x_m the executor's own remaining advance, and names EVERY failing
#     requirement otherwise, in the fixed order of REASONS;
#   - wire_v2() is EXACTLY the schema (autonomy/schemas/target_observation.v2.schema.json), checked by a minimal validator
#     written against the schema file: required, additionalProperties, types, consts, ranges, patterns;
#   - max_age_ns is the perception deadline plus the relay's bounded waits, and the two timeouts, the advance bound and the
#     contract words are the executor's and the capability's own;
#   - capability_observation_available() is (True, None);
#   - check_for_pulse, the rule the relay applies, against the observation's own bound;
#   - ObservedTarget: identity and evidence are ids and numbers only, and malformed values raise;
#   - the module imports only the standard library, flat or as a package.
# No robot, no camera, no network, no model. Run directly: python3 autonomy/tests/test_target_observation_source.py
import json
import math
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MOTION = os.path.join(ROOT, "autonomy", "motion")
SCHEMA = os.path.join(ROOT, "autonomy", "schemas", "target_observation.v2.schema.json")
sys.path.insert(0, MOTION)
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))

import target_observation_source as tos  # noqa: E402

NOW = 100_000_000_000  # 100 s on the monotonic clock: more than the 75 s bound a 60 s budget gives, so a stale stamp is still a stamp
DEADLINE_S = 60.0
NEED_MM = 65.0
TARGET = "cube-b"
OBSERVATION_ID = "obs-20260926T000000Z-wrist-0001"


def approach(**changes) -> dict:
    """The approach state run_single_cube_cycle.advance_base keeps: the view's id, the anchor and the reach axis in PAD_FRAME,
    the remaining advance and the view's pad validity figures."""
    state = {"observation_id": OBSERVATION_ID, "anchor_pad_m": [0.0145, -0.1825], "axis_pad": [0.0, 1.0], "need_mm": NEED_MM,
             "pad_frame_valid": True, "landmark_inliers": 41, "reprojection_rms_px": 1.2}
    state.update(changes)
    return state


def capture(**changes) -> dict:
    """The latest stamped capture as the controller stores it (Cycle._capture with its identity filled in)."""
    record = {"observation_id": OBSERVATION_ID, "capture_monotonic_ns": NOW - 3_000_000_000, "motion_epoch": 7, "stationary": True,
              "frame_sha256": "a" * 64, "run_id": "console-20260926T000000Z-00000000", "perception_request_id": "0123456789abcdef0123456789abcdef"}
    record.update(changes)
    return record


COMPLETE = object()  # "the complete default", so that None can mean "no state"


def resolve(approach_state=COMPLETE, record=COMPLETE, selection=object(), now=NOW, deadline=DEADLINE_S, target_id=TARGET):
    return tos.resolve(approach() if approach_state is COMPLETE else approach_state, capture() if record is COMPLETE else record, selection, now,
                       perception_deadline_s=deadline, target_id=target_id)


def observed(**changes) -> tos.ObservedTarget:
    fields = dict(target_id=TARGET, x_m=NEED_MM / 1000.0, capture_monotonic_ns=NOW, motion_epoch=3, observation_id=OBSERVATION_ID,
                  max_age_ns=tos.max_age_ns_for(DEADLINE_S), stationary=True)
    fields.update(changes)
    return tos.ObservedTarget(**fields)


def validate_against_schema(document: dict, schema: dict) -> list:
    """A minimal validator for the subset of JSON Schema the v2 schema uses: type object, required, additionalProperties
    false, and per property type (string, integer, number), const, minimum, maximum, exclusiveMinimum, pattern. Returns
    the list of violations, so a test can say which one."""
    problems = []
    if not isinstance(document, dict):
        return ["not an object"]
    properties = schema["properties"]
    for name in schema["required"]:
        if name not in document:
            problems.append(f"missing {name}")
    if schema.get("additionalProperties") is False:
        problems.extend(f"unknown {name}" for name in document if name not in properties)
    for name, rule in properties.items():
        if name not in document:
            continue
        value = document[name]
        if "const" in rule:
            if value != rule["const"] or type(value) is not type(rule["const"]):
                problems.append(f"{name} is not the const {rule['const']!r}")
            continue
        kind = rule.get("type")
        if kind == "string" and not isinstance(value, str):
            problems.append(f"{name} is not a string")
        elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            problems.append(f"{name} is not an integer")
        elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            problems.append(f"{name} is not a finite number")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                problems.append(f"{name} below its minimum")
            if "exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]:
                problems.append(f"{name} not above its exclusive minimum")
            if "maximum" in rule and value > rule["maximum"]:
                problems.append(f"{name} above its maximum")
        if "pattern" in rule and isinstance(value, str) and re.match(rule["pattern"], value) is None:
            problems.append(f"{name} does not match its pattern")
    return problems


class ResolveReturnsTheExecutorsOwnQuantityOrNamesEveryReason(unittest.TestCase):
    def reasons(self, *args, **kwargs) -> list:
        with self.assertRaises(tos.ObservationUnavailable) as raised:
            resolve(*args, **kwargs)
        return list(raised.exception.reasons)

    def test_a_complete_state_resolves_to_the_remaining_advance_in_the_anchor_reach_frame(self):
        target = resolve()
        self.assertIsInstance(target, tos.ObservedTarget)
        self.assertEqual((TARGET, NEED_MM / 1000.0, 0.0, "anchor_reach", True), (target.target_id, target.x_m, target.y_m, target.frame, target.stationary))
        self.assertEqual((NOW - 3_000_000_000, 7, OBSERVATION_ID), (target.capture_monotonic_ns, target.motion_epoch, target.observation_id))
        self.assertEqual(tos.max_age_ns_for(DEADLINE_S), target.max_age_ns)
        self.assertEqual(((0.0145, -0.1825), (0.0, 1.0), NEED_MM, 41, 1.2), (target.anchor_pad_m, target.axis_pad, target.need_mm, target.landmark_inliers, target.reprojection_rms_px))
        self.assertEqual(("a" * 64, "console-20260926T000000Z-00000000", "0123456789abcdef0123456789abcdef"), (target.frame_sha256, target.run_id, target.perception_request_id))
        self.assertEqual("anchor_reach", tos.CONTRACT_FRAME)

    def test_the_identity_stays_optional_and_the_figures_may_be_absent(self):
        target = resolve(approach(landmark_inliers=None, reprojection_rms_px=None), capture(frame_sha256=None, run_id=None, perception_request_id=None))
        self.assertIsNone(target.frame_sha256)
        self.assertIsNone(target.landmark_inliers)
        self.assertEqual(NEED_MM / 1000.0, target.x_m)

    def test_nothing_at_all_names_the_four_reasons_that_can_be_named_in_the_fixed_order(self):
        self.assertEqual([tos.REASON_NOT_SELECTED, tos.REASON_NO_OBSERVATION, tos.REASON_APPROACH_NOT_MEASURED, tos.REASON_PERCEPTION_BUDGET_UNKNOWN],
                         self.reasons(None, None, selection=None, deadline=None))
        self.assertEqual([tos.REASON_NOT_SELECTED, tos.REASON_NO_OBSERVATION, tos.REASON_APPROACH_NOT_MEASURED, tos.REASON_PERCEPTION_BUDGET_UNKNOWN],
                         self.reasons(False, "not a capture", selection=None, deadline=float("nan")))

    def test_every_reason_is_named_by_its_own_failing_requirement(self):
        for name, kwargs, code in (
                ("no selection", dict(selection=None), tos.REASON_NOT_SELECTED),
                ("no target id", dict(target_id=None), tos.REASON_NOT_SELECTED),
                ("an empty target id", dict(target_id=""), tos.REASON_NOT_SELECTED),
                ("no capture", dict(record=None), tos.REASON_NO_OBSERVATION),
                ("a capture without a stamp", dict(record=capture(capture_monotonic_ns=None)), tos.REASON_CAPTURE_TIME_UNKNOWN),
                ("a capture with a fractional stamp", dict(record=capture(capture_monotonic_ns=1.5)), tos.REASON_CAPTURE_TIME_UNKNOWN),
                ("the approach view is another capture", dict(record=capture(observation_id="obs-20260926T000000Z-wrist-0002")), tos.REASON_NOT_APPROACH_VIEW),
                ("the base moved at the capture", dict(record=capture(stationary=False)), tos.REASON_NOT_STATIONARY),
                ("stationarity not recorded", dict(record=capture(stationary=None)), tos.REASON_NOT_STATIONARY),
                ("the pad frame is invalid", dict(approach_state=approach(pad_frame_valid=False)), tos.REASON_PAD_FRAME_INVALID),
                ("no approach state", dict(approach_state=None), tos.REASON_APPROACH_NOT_MEASURED),
                ("no bounded advance covers it", dict(approach_state=approach(need_mm=None)), tos.REASON_ADVANCE_OUT_OF_BOUNDS),
                ("a negative advance", dict(approach_state=approach(need_mm=-1.0)), tos.REASON_ADVANCE_OUT_OF_BOUNDS),
                ("more than the bounded total", dict(approach_state=approach(need_mm=tos.MAX_ADVANCE_MM + 1.0)), tos.REASON_ADVANCE_OUT_OF_BOUNDS),
                ("no perception budget", dict(deadline=None), tos.REASON_PERCEPTION_BUDGET_UNKNOWN),
                ("a zero perception budget", dict(deadline=0.0), tos.REASON_PERCEPTION_BUDGET_UNKNOWN),
                ("an infinite perception budget", dict(deadline=float("inf")), tos.REASON_PERCEPTION_BUDGET_UNKNOWN),
                ("a capture older than the budget", dict(record=capture(capture_monotonic_ns=NOW - tos.max_age_ns_for(DEADLINE_S) - 1)), tos.REASON_STALE),
                ("a capture from the future", dict(record=capture(capture_monotonic_ns=NOW + 1)), tos.REASON_STALE)):
            with self.subTest(name):
                self.assertEqual([code], self.reasons(**kwargs))

    def test_the_bounds_are_inclusive(self):
        self.assertEqual(0.0, resolve(approach(need_mm=0.0)).x_m)
        self.assertEqual(tos.MAX_ADVANCE_MM / 1000.0, resolve(approach(need_mm=tos.MAX_ADVANCE_MM)).x_m)
        edge = resolve(record=capture(capture_monotonic_ns=NOW - tos.max_age_ns_for(DEADLINE_S)))
        self.assertEqual(NOW - tos.max_age_ns_for(DEADLINE_S), edge.capture_monotonic_ns)
        self.assertEqual(NOW, resolve(record=capture(capture_monotonic_ns=NOW)).capture_monotonic_ns)

    def test_several_failing_requirements_are_all_named_in_the_fixed_order(self):
        reasons = self.reasons(approach(pad_frame_valid=False, need_mm=None), capture(stationary=False, observation_id="obs-other"), selection=None, deadline=None)
        self.assertEqual([tos.REASON_NOT_SELECTED, tos.REASON_NOT_APPROACH_VIEW, tos.REASON_NOT_STATIONARY, tos.REASON_PAD_FRAME_INVALID,
                          tos.REASON_ADVANCE_OUT_OF_BOUNDS, tos.REASON_PERCEPTION_BUDGET_UNKNOWN], reasons)
        self.assertEqual([reason for reason in tos.REASONS if reason in reasons], reasons, "not in the fixed order")
        self.assertEqual(10, len(tos.REASONS))
        self.assertEqual(len(set(tos.REASONS)), len(tos.REASONS))

    def test_a_stale_capture_is_judged_only_when_a_stamp_and_a_budget_exist(self):
        self.assertNotIn(tos.REASON_STALE, self.reasons(record=capture(capture_monotonic_ns=None)))
        self.assertNotIn(tos.REASON_STALE, self.reasons(record=capture(capture_monotonic_ns=NOW - 10 ** 15), deadline=None))

    def test_the_message_names_codes_and_never_a_value(self):
        marker = "/private-path-" + "x" * 8
        with self.assertRaises(tos.ObservationUnavailable) as raised:
            resolve(approach(observation_id=marker), capture(observation_id=marker + "2", stationary=False), deadline=None)
        self.assertNotIn(marker, str(raised.exception))
        self.assertRegex(str(raised.exception), r"^[a-z0-9_.,-]+$")
        with self.assertRaises(ValueError):
            tos.ObservationUnavailable([])
        with self.assertRaises(ValueError):
            tos.ObservationUnavailable(["Not A Code"])

    def test_a_mapping_exists_so_availability_is_true(self):
        self.assertEqual((True, None), tos.capability_observation_available())


class TheFreshnessBoundAndTheCopiedConstants(unittest.TestCase):
    def test_max_age_is_the_perception_deadline_plus_two_admin_reads_and_the_link_wait(self):
        self.assertEqual(round((60.0 + 2 * 5.0 + 5.0) * 1e9), tos.max_age_ns_for(60.0))
        self.assertEqual(round((0.5 + 15.0) * 1e9), tos.max_age_ns_for(0.5))
        self.assertIsInstance(tos.max_age_ns_for(12.0), int)
        for bad in (None, 0.0, -1.0, float("nan"), float("inf"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tos.max_age_ns_for(bad)

    def test_the_copies_equal_the_executors_constants(self):
        """The module imports no numpy, so these are copies; imported the way test_run_single_cube_cycle imports the executor."""
        import run_single_cube_cycle as cycle

        self.assertEqual(cycle.CAPABILITY_ADMIN_TIMEOUT_S, tos.CAPABILITY_ADMIN_TIMEOUT_S)
        self.assertEqual(cycle.CAPABILITY_LINK_TIMEOUT_S, tos.CAPABILITY_LINK_TIMEOUT_S)
        self.assertEqual(cycle.BASE_MAX_TOTAL_ADVANCE_MM, tos.MAX_ADVANCE_MM)
        self.assertEqual(tos.max_age_ns_for(60.0), round((60.0 + 2 * cycle.CAPABILITY_ADMIN_TIMEOUT_S + cycle.CAPABILITY_LINK_TIMEOUT_S) * 1e9))

    def test_the_contract_words_are_the_capabilitys_own(self):
        sys.path.insert(0, ROOT)
        from autonomy.motion import mobile_approach

        self.assertEqual(mobile_approach.CONTRACT_V2, tos.CONTRACT)
        self.assertEqual(mobile_approach.FRAME_ANCHOR_REACH, tos.CONTRACT_FRAME)
        self.assertEqual(mobile_approach.V2_MAX_ADVANCE_M, tos.MAX_ADVANCE_MM / 1000.0)


class TheRuleThePulseApplies(unittest.TestCase):
    def test_each_requirement(self):
        age = tos.max_age_ns_for(DEADLINE_S)
        self.assertIsNone(tos.check_for_pulse(observed(), target_id=TARGET, motion_epoch=3, now_ns=NOW + age))
        self.assertIsNone(tos.check_for_pulse(observed(), target_id=TARGET, motion_epoch=3, now_ns=NOW))
        for name, thing, epoch, now, code in (("another target", observed(target_id="cube-a"), 3, NOW, tos.PULSE_TARGET_MISMATCH),
                                              ("the v1 frame", observed(frame="base"), 3, NOW, tos.PULSE_FRAME_NOT_ANCHOR_REACH),
                                              ("not stationary", observed(stationary=False), 3, NOW, tos.PULSE_NOT_STATIONARY),
                                              ("a changed epoch", observed(), 4, NOW, tos.PULSE_EPOCH_CHANGED),
                                              ("older than its bound", observed(), 3, NOW + age + 1, tos.PULSE_STALE),
                                              ("from the future", observed(), 3, NOW - 1, tos.PULSE_STALE),
                                              ("a wire document", observed().wire_v2(NOW), 3, NOW, tos.PULSE_NOT_AN_OBSERVATION),
                                              ("nothing", None, 3, NOW, tos.PULSE_NOT_AN_OBSERVATION)):
            with self.subTest(name):
                self.assertEqual(code, tos.check_for_pulse(thing, target_id=TARGET, motion_epoch=epoch, now_ns=now))

    def test_the_bound_is_the_observations_own_not_a_module_constant(self):
        short, long = observed(max_age_ns=200_000_000), observed(max_age_ns=tos.max_age_ns_for(DEADLINE_S))
        self.assertEqual(tos.PULSE_STALE, tos.check_for_pulse(short, target_id=TARGET, motion_epoch=3, now_ns=NOW + 200_000_001))
        self.assertIsNone(tos.check_for_pulse(long, target_id=TARGET, motion_epoch=3, now_ns=NOW + 200_000_001))


class TheObservedTarget(unittest.TestCase):
    def setUp(self):
        with open(SCHEMA, encoding="utf-8") as handle:
            self.schema = json.load(handle)

    def test_wire_v2_is_exactly_the_schema(self):
        published = NOW + 5_000_000
        document = observed().wire_v2(published)
        self.assertEqual([], validate_against_schema(document, self.schema))
        self.assertEqual(list(self.schema["required"]), list(document), "the eleven fields, in the schema's order, and nothing else")
        self.assertEqual(list(tos.WIRE_FIELDS), list(self.schema["required"]))
        self.assertEqual({"contract": "kc.target_observation/2", "target_id": TARGET, "capture_ns": NOW, "published_ns": published, "motion_epoch": 3,
                          "frame": "anchor_reach", "x_m": NEED_MM / 1000.0, "y_m": 0.0, "max_age_ns": tos.max_age_ns_for(DEADLINE_S), "stationary": True,
                          "observation_id": OBSERVATION_ID}, document)
        self.assertIs(document["stationary"], True)
        self.assertIsInstance(document["x_m"], float)
        json.dumps(document)  # serialisable as is

    def test_the_validator_is_not_vacuous(self):
        document = observed().wire_v2(NOW)
        for name, broken in (("a v1 document", {"target_id": TARGET, "timestamp_ns": NOW, "frame": "base", "x_m": 1.0, "y_m": 0.0, "quality": 0.9}),
                             ("an added field", {**document, "quality": 0.9}), ("the v1 frame", {**document, "frame": "base"}),
                             ("a lateral offset", {**document, "y_m": 0.01}), ("x beyond the bound", {**document, "x_m": 0.121}),
                             ("a stringly stamp", {**document, "capture_ns": str(NOW)}), ("a zero bound", {**document, "max_age_ns": 0}),
                             ("not stationary", {**document, "stationary": False}), ("a path as an id", {**document, "observation_id": "../x"})):
            with self.subTest(name):
                self.assertTrue(validate_against_schema(broken, self.schema))

    def test_a_malformed_publication_stamp_raises(self):
        for bad in (-1, 1.5, True, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                observed().wire_v2(bad)

    def test_its_identity_and_its_evidence_are_ids_and_numbers_only(self):
        target = observed(frame_sha256="a" * 64, run_id="console-1", perception_request_id="req-1", anchor_pad_m=(0.0145, -0.1825), axis_pad=(0.0, 1.0),
                          need_mm=NEED_MM, landmark_inliers=41, reprojection_rms_px=1.2)
        self.assertEqual({"observation_id", "frame_sha256", "run_id", "perception_request_id", "capture_monotonic_ns", "motion_epoch"}, set(target.identity()))
        self.assertEqual({"anchor_pad_m": [0.0145, -0.1825], "axis_pad": [0.0, 1.0], "need_mm": NEED_MM, "landmark_inliers": 41, "reprojection_rms_px": 1.2,
                          "max_age_ns": tos.max_age_ns_for(DEADLINE_S), "stationary": True}, target.evidence())
        self.assertNotIn("x_m", json.dumps(target.identity()))
        self.assertNotIn("/", json.dumps([target.identity(), target.evidence()]))

    def test_malformed_values_raise(self):
        for bad in (dict(target_id=""), dict(target_id="a/b"), dict(x_m=float("nan")), dict(y_m=float("inf")), dict(frame=None), dict(capture_monotonic_ns=-1),
                    dict(capture_monotonic_ns=1.5), dict(motion_epoch=True), dict(observation_id="../x"), dict(max_age_ns=0), dict(max_age_ns=True),
                    dict(max_age_ns=1.0), dict(stationary=1), dict(stationary=None), dict(anchor_pad_m=(0.0,)), dict(axis_pad=(0.0, float("nan"))),
                    dict(need_mm=float("nan")), dict(landmark_inliers=-1), dict(reprojection_rms_px=-0.1), dict(frame_sha256="ABC"), dict(run_id="a/b"),
                    dict(perception_request_id=7)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                observed(**bad)


class ImportSafety(unittest.TestCase):
    def test_flat_and_packaged_imports_load_only_the_standard_library(self):
        for code in ("import sys; sys.path.insert(0, sys.argv[1]); import target_observation_source as m",
                     "import sys; sys.path.insert(0, sys.argv[2]); from autonomy.motion import target_observation_source as m"):
            probe = code + ("; import json; print(json.dumps([sorted(sys.modules), m.capability_observation_available()]))")
            done = subprocess.run([sys.executable, "-c", probe, MOTION, ROOT], capture_output=True, text=True, timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            loaded, answer = json.loads(done.stdout)
            top = {name.split(".")[0] for name in loaded}
            self.assertFalse(top & {"numpy", "cv2", "zmq", "lerobot", "torch", "run_single_cube_cycle", "rehearsal_observation", "demo_runtime", "mobile_approach"}, top)
            self.assertEqual([True, None], answer)


if __name__ == "__main__":
    unittest.main()
