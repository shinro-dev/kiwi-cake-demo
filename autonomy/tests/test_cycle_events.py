#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# autonomy/tests/test_cycle_events.py: what the controller SAYS about a run, checked against the contract and against the
# code that produced it. The scripted world of test_cycle_behavior_preserved.py runs main() with KC_EVENTS_PATH set; every
# line of the resulting stream is validated against packages/demo-contracts, and then the claims that matter are read back
# and held against the place in the controller they came from: a predicate must name the observation the CODE looked at,
# not a neighbouring one that would look just as plausible in a record. Behaviour preservation is the other test's job;
# this one only asks whether the record is TRUE.
# No robot, no camera, no network, no lerobot, no model. Run directly: python3 autonomy/tests/test_cycle_events.py
import json
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "autonomy", "tests"))
sys.path.insert(0, os.path.join(ROOT, "bin", "laptop", "manipulation"))
sys.path.insert(0, os.path.join(ROOT, "packages", "demo-contracts", "python"))

import demo_contracts  # noqa: E402
import run_single_cube_cycle as cycle  # noqa: E402
import test_cycle_behavior_preserved as differential  # noqa: E402  (the scripted World, SyntheticCamera and run_scripted_world)
import test_run_single_cube_cycle as fixtures  # noqa: E402  (the synthetic runtime configuration and the synthetic low scan pose)


def label_of(planned: dict):
    """The label an action.planned carries, which is how an action is recognized without parsing stdout."""
    return next((item["value"] for item in planned["payload"]["parameters"] if item["name"] == "label"), None)


class Recorder:
    """An emitter that keeps what it was handed, for the few checks that drive CycleEvents directly instead of a whole run."""

    def __init__(self):
        self.records = []

    def emit(self, kind, payload, **envelope):
        self.records.append({"kind": kind, "payload": payload, "observation_id": envelope.get("observation_id"), **envelope})
        return len(self.records)


class RepeatingRobot:
    """A client that serves a given sequence of SAMPLES, one per read, repeating an entry where the stock client would have
    served its cache. Base velocity is zero throughout; only the identity of the sample it served is under test."""

    local_rx_session = "laptop-rx-0123456789ab"

    def __init__(self, served):
        self.served, self.reads = list(served), 0

    def get_observation(self):
        self.reads += 1
        return {key: 0.0 for key in cycle.cli.BASE_VELOCITY_KEYS}

    def stamp_for(self, sample_seq: int) -> dict:
        return {"session": self.local_rx_session, "seq": int(sample_seq), "sample_mono_ns": 1_000_000 * int(sample_seq), "clock_domain": "laptop_monotonic"}

    def last_stamp(self) -> dict:
        sample = self.served[self.reads - 1]
        return {"fresh": self.reads == 1 or sample != self.served[self.reads - 2], "local_rx_seq": sample, "availability": "host_stamped", "age_ns": 0}

    def to_source_stamp(self) -> dict:
        return self.stamp_for(self.served[self.reads - 1])


class EventRun:
    """One scripted cycle with events ON, and the stream it wrote, parsed."""

    def __init__(self, test, scenario: str = "a full base-advance placement", **options):
        self.directory = test.directory
        path = os.path.join(self.directory, "controller-events.jsonl")
        if os.path.exists(path):
            os.unlink(path)
        self.path = path
        self.run = differential.run_scripted_world(cycle, differential.World(**differential.WORLD_SCENARIOS[scenario]), self.directory, configured=True, events_path=path, **options)
        self.lines = []
        if os.path.exists(path):
            with open(path) as handle:
                self.lines = [json.loads(line) for line in handle if line.strip()]

    def of_kind(self, kind: str) -> list:
        return [line for line in self.lines if line["kind"] == kind]

    def predicates(self, name: str) -> list:
        return [line for line in self.of_kind("predicate.evaluated") if line["payload"]["predicate"] == name]

    def evidence(self, record: dict, kind: str) -> list:
        """The observation ids a predicate names under one evidence type, flattened in the order they were stated."""
        return [item for entry in record["payload"]["evidence"] if entry["type"] == kind for item in entry["observation_ids"]]

    def frames_by_action(self) -> dict:
        """action_id -> the ids of the stored frames captured while that action was the current one. An observation the
        controller took is stated inside the action that took it, which is what lets EXIT be told apart from SETTLE."""
        out = {}
        for line in self.of_kind("observation.captured"):
            if line["payload"]["media"] == "frame" and line["payload"]["retained"]:
                out.setdefault(line["action_id"], []).append(line["observation_id"])
        return out

    def action_labelled(self, prefix: str):
        return next((line["action_id"] for line in self.of_kind("action.planned") if (label_of(line) or "").startswith(prefix)), None)

    def finished(self) -> dict:
        return self.of_kind("run.finished")[0]

    def measurement(self, named: list, name: str):
        """One value out of a named_value list, by name. Raises when the list does not carry it, so a test cannot pass by
        asking for something that is simply not there."""
        return next(item["value"] for item in named if item["name"] == name)

    def ending_sentence(self) -> str:
        """The sentence the OPERATOR read on stdout for this ending, without the fixed frame around it. run.finished must
        carry that same sentence, so that the browser and the terminal never say different things about one failure."""
        line = next(item for item in self.run["stdout"].splitlines() if item.startswith(("CYCLE FAILED: ", "STOPPED: ", "REFUSED before any motion: ")))
        return line.split(": ", 1)[1].rsplit(". The arm HOLDS", 1)[0]


class EventCase(unittest.TestCase):
    def setUp(self):
        self.directory = fixtures.use_synthetic_runtime(self)
        cycle.use_runtime_paths(cycle.cli.runtime_paths({}))  # the fixture applied the configuration; each run below says for itself that it is configured


class ContractTest(EventCase):
    def test_every_line_of_a_full_cycle_is_a_valid_event_and_the_stream_is_ordered_and_whole(self):
        written = EventRun(self)
        self.assertEqual(("returned", 0), written.run["ending"], written.run["stdout"][-2000:])
        self.assertGreater(len(written.lines), 100, "a whole cycle that stated fewer than a hundred facts did not run")
        for line in written.lines:
            self.assertEqual([], demo_contracts.validate("event", line), json.dumps(line)[:400])
        self.assertEqual(list(range(len(written.lines))), [line["seq"] for line in written.lines], "the stream has a hole or is out of order")
        self.assertEqual(1, len(written.of_kind("run.started")))
        self.assertEqual(1, len(written.of_kind("run.finished")))
        self.assertEqual("run.finished", written.lines[-1]["kind"], "run.finished is not the last thing said")
        self.assertEqual({"1"}, {line["schema_version"] for line in written.lines})

    def test_no_line_carries_this_machine_or_this_run_s_private_values(self):
        """The emitter writes what it is given; the sanitizing is the controller's. The scripted run is told a documentation
        address and reserved identities, so what must not appear is exactly those."""
        written = EventRun(self)
        whole = "\n".join(json.dumps(line) for line in written.lines)
        for token in ("192.0.2.10", "synthetic-operator", os.path.expanduser("~"), self.directory):
            self.assertNotIn(token, whole, f"a private value of this run reached the record ({token[:12]})")

    def test_the_run_config_states_the_constants_this_module_really_used(self):
        written = EventRun(self)
        summary = {item["name"]: item["value"] for item in written.of_kind("run.config")[0]["payload"]["summary"]}
        self.assertEqual("single-cube-cycle", summary["controller"])
        self.assertIs(True, summary["runtime_config_given"])
        self.assertIs(True, summary["stamped_reads"])
        self.assertIs(False, summary["freshness_refusal"], "the configuration did not enable the refusal, so it must be off")
        # read from the live module, not from a copy: a threshold that is changed must show its changed value here
        for name in ("place_required_centre_mm", "placed_min_edge_margin_mm", "retention_min_score", "base_return_tolerance_mm"):
            self.assertEqual(getattr(cycle, name.upper()), summary[f"constant.{name}"], f"constant.{name} is not what the module holds")
        self.assertIs(True, summary["empirical.retention_min_score"], "a threshold measured on a few runs must be marked as such")
        self.assertNotIn("empirical.place_required_centre_mm", summary, "a DEFINED threshold must not be marked empirical")


class EvidenceTest(EventCase):
    """Whether a predicate names the observation the code looked at. Each of these has a neighbour that would pass a
    weaker test: SETTLE right after EXIT, the VERIFICATION lift right before the CLEARANCE one, a frame next to a grasp."""

    def setUp(self):
        super().setUp()
        self.written = EventRun(self)
        self.assertEqual(("returned", 0), self.written.run["ending"], self.written.run["stdout"][-2000:])

    def test_placed_rests_on_the_exit_observation_and_not_on_the_settle_one(self):
        frames = self.written.frames_by_action()
        exit_frames = frames[self.written.action_labelled("EXIT")]
        settle_frames = frames[self.written.action_labelled("SETTLE")]
        self.assertEqual(1, len(exit_frames))
        self.assertEqual(1, len(settle_frames))
        self.assertNotEqual(exit_frames, settle_frames, "the two observations are the same: this test could not tell them apart")
        for name in ("placed", "clearance_target_met"):
            named = self.written.evidence(self.written.predicates(name)[-1], "frame")
            self.assertEqual(exit_frames, named, f"{name} does not rest on the EXIT observation")
            self.assertNotIn(settle_frames[0], named, f"{name} rests on the SETTLE observation, which the code never looks at")

    def test_object_secured_rests_on_the_two_lift_frames_the_score_was_taken_between(self):
        frames = self.written.frames_by_action()
        secured = self.written.predicates("object_secured")[-1]
        pair = self.written.evidence(secured, "frame_pair")
        self.assertEqual(frames[self.written.action_labelled("CLEARANCE")] + frames[self.written.action_labelled("CONFIRM")], pair,
                         "the retention score is taken between the CLEARANCE and the CONFIRM lift, in that order")
        self.assertNotIn(frames[self.written.action_labelled("VERIFICATION")][0], pair, "the first lift is not one of the scored frames")
        self.assertEqual([], self.written.evidence(secured, "frame"))
        self.assertEqual(cycle.RETENTION_MIN_SCORE, secured["payload"]["threshold"])
        self.assertIs(True, secured["payload"]["value"])

    def test_the_grasp_predicate_is_a_scalar_readback_and_names_no_frame_at_all(self):
        closed = self.written.predicates("closed_on_something")
        self.assertTrue(closed, "the cycle never closed the gripper")
        for record in closed:
            self.assertEqual([], [entry for entry in record["payload"]["evidence"] if entry["type"] != "scalar"], "a gripper readback is not seen in any frame")
            self.assertEqual([], [item for entry in record["payload"]["evidence"] for item in entry["observation_ids"]])
            self.assertIsNone(record["observation_id"])
            names = {entry["scalar_name"] for entry in record["payload"]["evidence"]}
            self.assertEqual({"gripper_readback", "gripper_latch_value", "gripper_empty_latch_margin", "gripper_holding_band_max"}, names)
            self.assertEqual(cycle.GRIPPER_LATCH + cycle.GRIPPER_EMPTY_LATCH_MARGIN, record["payload"]["threshold"])
            self.assertEqual(record["payload"]["value"], cycle.closed_on_something(record["payload"]["measured"]), "the stated value is not what the module's own function says of the stated reading")

    def test_straddling_is_the_measured_tool_height_the_code_gates_on(self):
        """The docstring of that state speaks of the cube between the jaws; the CODE compares a tool height. The record
        must say what was decided, so the predicate is a scalar and its measured value is the height."""
        straddling = self.written.predicates("straddling")[-1]
        self.assertEqual([], [entry for entry in straddling["payload"]["evidence"] if entry["type"] != "scalar"])
        self.assertEqual("mm", straddling["payload"]["unit"])
        self.assertEqual(8.0, straddling["payload"]["threshold"])
        self.assertIs(True, straddling["payload"]["value"])
        self.assertLessEqual(straddling["payload"]["measured"], 8.0, "the value says the gate passed; the measurement must agree")

    def test_the_pad_frame_pairs_behind_a_measured_base_displacement_are_named_as_pad_measurements(self):
        measured = self.written.predicates("base_measured")
        self.assertTrue(measured, "the base never moved in this scenario")
        for record in measured:
            self.assertEqual(2, len(self.written.evidence(record, "pad_measurement")), "a displacement is measured between exactly two pad frames")
        released = self.written.predicates("release_margin")[-1]
        self.assertTrue(set(self.written.evidence(measured[0], "pad_measurement")) <= set(self.written.evidence(released, "pad_measurement")),
                        "the release margin carries the anchor forward through every measured displacement and must name them")
        self.assertEqual(sorted(set(self.written.evidence(released, "pad_measurement"))), sorted(self.written.evidence(released, "pad_measurement")), "a pad frame is named twice")

    def test_each_predicate_that_names_a_frame_names_an_observation_that_was_really_captured(self):
        stored = {line["observation_id"] for line in self.written.of_kind("observation.captured")}
        for record in self.written.of_kind("predicate.evaluated"):
            for entry in record["payload"]["evidence"]:
                for observation_id in entry["observation_ids"]:
                    self.assertIn(observation_id, stored, f"{record['payload']['predicate']} names {observation_id}, which was never stated as captured")


class BaseZeroTest(EventCase):
    def test_every_base_zero_evaluation_carries_the_stamp_of_each_read_it_consumed(self):
        written = EventRun(self)
        guard = sys.modules[cycle.run_scan_world.__module__]
        evaluations = written.predicates("base_zero")
        self.assertGreater(len(evaluations), 5, "a whole cycle evaluates the base-zero guard many times")
        samples = {line["observation_id"]: line for line in written.of_kind("observation.captured") if line["payload"]["media"] == "scalar_set"}
        for record in evaluations:
            per_read = [entry["observation_ids"] for entry in record["payload"]["evidence"] if entry["scalar_name"] == "base_velocity_reads"][0]
            fresh = [entry["observation_ids"] for entry in record["payload"]["evidence"] if entry["scalar_name"] == "base_velocity_fresh_reads"][0]
            self.assertGreaterEqual(len(per_read), guard.BASE_ZERO_CONSECUTIVE_READS, "an evaluation is stated with fewer reads than the guard takes")
            self.assertTrue(set(fresh) <= set(per_read), "a read reported fresh was not one of the reads")
            self.assertEqual("reads", record["payload"]["unit"])
            self.assertEqual(float(guard.BASE_ZERO_CONSECUTIVE_READS), record["payload"]["threshold"])
            for observation_id in per_read:
                self.assertIn(observation_id, samples, "a read of the guard was not stated as its own base sample")
                stamp = samples[observation_id]["payload"]["source_stamp"]
                self.assertIsNotNone(stamp, "the scripted host stamps its messages, so every sample must carry a stamp")
                self.assertEqual([], demo_contracts.validate("common#/$defs/source_stamp", stamp))
            self.assertEqual(samples[per_read[-1]]["payload"]["source_stamp"], record["payload"]["consumed_source_stamp"],
                             "the one stamp the contract has room for must be the LAST read the decision consumed")

    def test_each_base_sample_of_a_whole_cycle_is_stated_as_captured_exactly_once(self):
        written = EventRun(self)
        stated = [line["observation_id"] for line in written.of_kind("observation.captured") if line["payload"]["media"] == "scalar_set"]
        self.assertEqual(len(stated), len(set(stated)), "a base sample was stated as captured more than once")
        self.assertGreater(len(stated), 50)

    def test_a_sample_served_to_two_reads_is_named_twice_so_the_distinct_ids_are_the_distinct_samples(self):
        """The scripted host of the world above mints a new sample for every poll, so a REPEAT never happens there. On the
        robot it does: the stock client serves its cached observation whenever the poll brought nothing. The rule that a
        record must still show how many distinct samples a decision rested on is therefore checked here, where a repeat
        can be scripted: two reads that consumed one sample carry one id, twice."""
        guard = sys.modules[cycle.run_scan_world.__module__]
        served = [11, 11, 12]  # the middle read was served the sample the first one already had
        recorder = Recorder()
        events = cycle.CycleEvents(recorder)
        robot = RepeatingRobot(served)
        source = events.reads(robot)
        read_base = events.base_reads(lambda: cycle.cli.read_base(source))
        sleep = events.polls(lambda _seconds: None)
        for index in range(len(served)):
            read_base()
            if index < len(served) - 1:
                sleep(guard.BASE_ZERO_POLL_S)  # the guard's own pause: what tells one evaluation from the next
        events._close_base_group()  # noqa: SLF001 -- in a run this is done by the next thing that is not a base read
        evaluation = next(record for record in recorder.records if record["kind"] == "predicate.evaluated" and record["payload"]["predicate"] == "base_zero")
        named = [entry["observation_ids"] for entry in evaluation["payload"]["evidence"] if entry["scalar_name"] == "base_velocity_reads"][0]
        self.assertEqual(3, len(named), "one id per READ, in read order")
        self.assertEqual(2, len(set(named)), "three reads consumed two samples, so the distinct ids must be two")
        self.assertEqual(named[0], named[1], "the two reads that consumed one sample must carry one id")
        captured = [record["observation_id"] for record in recorder.records if record["kind"] == "observation.captured"]
        self.assertEqual(sorted(set(named)), sorted(captured), "a sample is stated as captured once, however many reads it was served to")
        self.assertEqual(robot.stamp_for(12), evaluation["payload"]["consumed_source_stamp"])
        self.assertEqual(0, events.hook_errors)

    def test_two_base_pulses_whose_reads_carry_no_stamp_do_not_share_one_name(self):
        """The reads a base primitive's own client served are handed over per pulse and cleared between pulses. When such a
        read carries no stamp (a fresh client that has not adopted a message yet serves an empty one), its name can only
        come from the read, and it must still be unique for the RUN: otherwise the second pulse's stop confirmation names
        the FIRST pulse's samples, and a reader cannot tell that from the truth."""
        def unstamped():  # what PrimitiveClient.get_observation() keeps for a read that was served no new message
            return {"observation": {key: 0.0 for key in cycle.cli.BASE_VELOCITY_KEYS}, "stamp": None, "session": "laptop-rx-0123456789ab",
                    "detail": {"fresh": False, "local_rx_seq": 0, "local_rx_mono_ns": None, "age_ns": None, "host": None, "availability": "no_message_yet"}}

        recorder = Recorder()
        events = cycle.CycleEvents(recorder)
        move_base, named = mock.Mock(spec=["consumed_reads"]), []
        for _pulse in range(2):
            move_base.consumed_reads = [unstamped() for _ in range(3)]
            events.base_stop_confirmed(True, move_base)
            confirmation = [record for record in recorder.records if record["kind"] == "predicate.evaluated"][-1]
            named.append([entry["observation_ids"] for entry in confirmation["payload"]["evidence"] if entry["scalar_name"] == "base_velocity_reads"][0])
        self.assertEqual([3, 3], [len(ids) for ids in named])
        self.assertEqual(6, len(set(named[0] + named[1])), "six reads of two different clients were given fewer than six names")
        captured = [record["observation_id"] for record in recorder.records if record["kind"] == "observation.captured"]
        self.assertEqual(sorted(named[0] + named[1]), sorted(captured), "a named read was not stated as captured, or was stated twice")
        self.assertEqual(0, events.hook_errors)


class PerceptionLayerTest(EventCase):
    """One perception.result per LAYER, each with its own validity: a layer that could not run says so, and never borrows
    the verdict of another layer of the same frame."""

    def test_a_frame_without_pad_frame_leaves_the_cube_box_alone_and_the_pad_layer_says_it_could_not_run(self):
        written = EventRun(self)
        by_observation = {}
        for line in written.of_kind("perception.result"):
            by_observation.setdefault(line["observation_id"], {})[line["payload"]["detector"]] = line["payload"]
        self.assertGreater(len(by_observation), 10, "a whole cycle perceives many frames")
        with_frame, without_frame = 0, 0
        for layers in by_observation.values():
            self.assertEqual({"cube-recognizer", "pad-outline-fit", "pad-print-landmarks", "cube-in-pad-frame"}, set(layers), "a frame was not described layer by layer")
            landmarks = {item["name"]: item["value"] for item in layers["pad-print-landmarks"]["measurements"]}
            self.assertEqual("detected", layers["cube-recognizer"]["result"], "the scripted world always sees the cube; no other layer may change that")
            if landmarks.get("pad_frame_valid") is True:
                with_frame += 1
                self.assertNotEqual("error", layers["cube-in-pad-frame"]["result"], "PAD_FRAME was fixed, so that layer did run")
            else:
                without_frame += 1
                self.assertEqual("error", layers["cube-in-pad-frame"]["result"], "a layer that never ran must not report that the cube was not on the pad")
                self.assertIsNone({item["name"]: item["value"] for item in layers["cube-in-pad-frame"]["measurements"]}["inside_pad_outline"])
        self.assertGreater(with_frame, 0, "no frame of this run fixed PAD_FRAME")
        self.assertGreater(without_frame, 0, "every frame fixed PAD_FRAME: this test could not tell the two cases apart")


class RunIdTest(EventCase):
    def test_no_emitter_is_started_when_nothing_names_the_run(self):
        """run.started, run.config and run.finished pin run_id to an id. An emitter built without one would write those
        three records, all invalid, and nothing later could repair them; starting none at all is the honest end."""
        class Args:
            remote_ip, robot_id, pi_ssh, plan_only, holding, do, scan_id = "192.0.2.10", "synthetic-unit", "", False, False, "cycle", ""

        refused, path = os.path.join(self.directory, "nameless-run.jsonl"), os.path.join(self.directory, "named-run.jsonl")
        with mock.patch.dict(os.environ, {"KC_EVENTS_PATH": refused, "KC_RUN_ID": ""}):
            nameless = cycle.start_events(Args())
        Args.scan_id = "manipulation-20260920"
        with mock.patch.dict(os.environ, {"KC_EVENTS_PATH": path, "KC_RUN_ID": ""}):
            named = cycle.start_events(Args())
        self.assertIs(False, nameless.enabled, "an emitter was started for a run that nothing names")
        self.assertFalse(os.path.exists(refused), "the refused emitter opened its file anyway")
        self.assertIs(True, named.enabled, "the scan id names the run, so the emitter must start")
        named.run_started(Args())
        named.finish(0)
        with open(path) as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(["run.started", "run.finished"], [line["kind"] for line in lines])
        for line in lines:
            self.assertEqual("manipulation-20260920", line["run_id"], "the run id the contract requires is not the one that names this run")
            self.assertEqual([], demo_contracts.validate("event", line), json.dumps(line)[:300])


class OutcomeTest(EventCase):
    def test_a_run_that_met_every_predicate_is_a_placement_and_carries_all_four(self):
        # placed_new is decided by _PLACEMENT_PREDICATES; this scenario measures all four true, so it is both a placement
        # and a fully accepted one. Which of the four decides the WORD is TerminalOutcomeTest's question, over every branch.
        written = EventRun(self)
        finished = written.of_kind("run.finished")[0]["payload"]
        self.assertEqual("placed_new", finished["outcome"])
        self.assertEqual({"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True}, finished["predicates"])
        self.assertEqual(0, finished["exit_status"])
        self.assertEqual(0, finished["task_control_interventions"], "nothing asked this run to stop")
        self.assertIs(True, finished["evidence_complete"])
        self.assertEqual(0, finished["evidence_gaps"])

    def test_a_cube_already_on_the_pad_is_a_no_op_and_not_a_placement(self):
        written = EventRun(self, "a cube that is already on the pad")
        self.assertEqual(("returned", 0), written.run["ending"])
        finished = written.of_kind("run.finished")[0]["payload"]
        self.assertEqual("already_placed_noop", finished["outcome"], "a run that commanded nothing must never be recorded as a placement")
        self.assertIs(True, finished["predicates"]["placed"])
        self.assertIsNone(finished["predicates"]["object_secured"], "nothing was ever grasped, so that predicate has no value")
        self.assertEqual([], written.run["sent"])
        self.assertEqual("DONE_ALREADY_PLACED", written.of_kind("state.transition")[-1]["payload"]["to_state"])

    def test_a_run_that_fails_its_evidence_is_not_placed_new_even_though_it_reached_the_end(self):
        written = EventRun(self, "a release the evidence does not admit")
        finished = written.of_kind("run.finished")[0]["payload"]
        self.assertEqual(5, finished["exit_status"])
        self.assertEqual("failed", finished["outcome"])
        self.assertIsNot(True, finished["predicates"]["placed"])
        self.assertIs(False, written.predicates("release_admitted")[-1]["payload"]["value"])

    def test_the_built_in_grasp_retry_is_counted_as_an_automatic_retry_and_not_as_an_intervention(self):
        written = EventRun(self, "an empty close, the bounded retry, then a hold")
        finished = written.of_kind("run.finished")[0]["payload"]
        self.assertEqual(0, finished["task_control_interventions"], "the controller's own bounded retry is not an operator acting on the run")
        self.assertGreater(finished["automatic_retries"], 0)
        self.assertEqual(2, len(written.predicates("closed_on_something")), "the scenario closes twice: once empty, once on the cube")
        self.assertEqual([False, True], [record["payload"]["value"] for record in written.predicates("closed_on_something")])


FAILURE_FAMILIES = {
    # the scripted scene -> the reason code the RAISE SITE mints, the exit status and the outcome of the contract.
    # One per family of failure exit the cycle has; the code is never read back out of the sentence, which is what
    # ReasonCodeTest below checks against the source itself.
    "a cube this cycle could not place, decided before the pickup": ("search_placement_infeasible", 5, "failed"),
    "three empty closes at a reach that admits both advances": ("grasp_missed", 5, "failed"),
    "a cube that does not rise with the jaws": ("retention_not_demonstrated", 5, "failed"),
    "a release the evidence does not admit": ("release_not_admitted", 5, "failed"),
    "a base pulse the two pad frames do not measure": ("base_measurement_implausible", 5, "failed"),
    "an operator abort during a base pulse": ("operator_abort_base_path", 5, "stopped"),
    "jaws that do not reopen after an empty close": ("step_refused_mid_cycle", 5, "failed"),
    # The executor's own ending. This module does not raise it and has no code of its own for it: the fallback names it
    # from the sentence scan_world produced, which is the one thing that names it, and run.finished says so too.
    "an operator abort while an arm segment runs": ("operator_abort", 4, "stopped"),
}


class TerminalOutcomeTest(unittest.TestCase):
    """The terminal word, taken from the ONE helper that decides it, over every branch it has. A placement is
    object_secured, placed and base_returned; clearance_target_met is a QUALITY fact measured beside them, which is why a
    run that placed short of the target is a placement carrying a false predicate and not a run nobody can describe.

    The regression case is physical: `console-20260922T083804Z-zxkydauq`, 2026-09-22, exit 0, phase BASE_RETURNED, the
    cube secured, placed and the base returned, the footprint clearance 2.39 mm short of its target. It said `unknown`,
    which the contract reserves for a run nothing established. Its retained record is NOT rewritten; this is the case
    that record was evidence for."""

    PLACED_SHORT = {"object_secured": True, "placed": True, "clearance_target_met": False, "base_returned": True}
    ALL_FOUR = {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True}

    def word(self, exit_status, predicates, *, already_placed=False, stop_path=False):
        return cycle._terminal_outcome(exit_status, already_placed, predicates, stop_path)

    def test_the_preserved_run_placed_and_returned(self):
        self.assertEqual("placed_new", self.word(0, self.PLACED_SHORT))

    def test_the_clearance_target_does_not_decide_whether_a_cube_was_placed(self):
        self.assertEqual(self.word(0, self.ALL_FOUR), self.word(0, self.PLACED_SHORT))

    def test_a_physical_predicate_that_measured_false_is_not_a_placement(self):
        for name in ("object_secured", "placed", "base_returned"):
            with self.subTest(name):
                self.assertEqual("unknown", self.word(0, {**self.ALL_FOUR, name: False}))

    def test_a_physical_predicate_that_was_never_evaluated_is_not_a_placement(self):
        for name in ("object_secured", "placed", "base_returned"):
            with self.subTest(name):
                self.assertEqual("unknown", self.word(0, {**self.ALL_FOUR, name: None}), "null is not evaluated, which is not true")

    def test_a_clearance_target_that_was_never_evaluated_still_leaves_a_placement(self):
        self.assertEqual("placed_new", self.word(0, {**self.ALL_FOUR, "clearance_target_met": None}))

    def test_a_cube_already_on_the_pad_wins_over_every_predicate(self):
        self.assertEqual("already_placed_noop", self.word(0, self.ALL_FOUR, already_placed=True))
        self.assertEqual("already_placed_noop", self.word(0, dict.fromkeys(cycle._RUN_PREDICATES), already_placed=True))

    def test_the_endings_of_a_nonzero_exit_are_unchanged(self):
        for exit_status, expected in ((3, "refused"), (4, "stopped"), (5, "failed"), (1, "failed"), (None, "failed")):
            with self.subTest(exit_status):
                self.assertEqual(expected, self.word(exit_status, self.ALL_FOUR))
        self.assertEqual("stopped", self.word(5, self.ALL_FOUR, stop_path=True), "a failure that FOLLOWED an observed stop is the stop")

    def test_a_nonzero_exit_never_consults_the_predicates(self):
        for exit_status in (3, 4, 5, 1):
            with self.subTest(exit_status):
                self.assertEqual(self.word(exit_status, self.ALL_FOUR), self.word(exit_status, dict.fromkeys(cycle._RUN_PREDICATES)),
                                 "the predicates must not reach a branch that already knows how the run ended")

    def test_already_placed_is_only_consulted_on_a_clean_exit(self):
        self.assertEqual("failed", self.word(5, self.ALL_FOUR, already_placed=True))

    def test_every_word_it_can_say_is_one_the_contract_defines(self):
        legal = set(demo_contracts.load_schema("event")["$defs"]["run_outcome"]["enum"])
        said = {self.word(exit_status, predicates, already_placed=already, stop_path=stop)
                for exit_status in (0, 1, 3, 4, 5, None) for already in (False, True) for stop in (False, True)
                for predicates in (self.ALL_FOUR, self.PLACED_SHORT, dict.fromkeys(cycle._RUN_PREDICATES))}
        self.assertLessEqual(said, legal, "the controller invented a word the contract does not define")


class FailureTest(EventCase):
    """Every way the cycle can END BADLY, read from the record alone. A console that never opens a terminal has to be able
    to say which physical check refused, in which state, on which numbers and against which evidence."""

    def failed(self, scenario: str) -> EventRun:
        code, status, outcome = FAILURE_FAMILIES[scenario]
        written = EventRun(self, scenario)
        self.assertEqual(("returned", status), written.run["ending"], written.run["stdout"][-2000:])
        payload = written.finished()["payload"]
        self.assertEqual(code, payload["reason_code"], f"{scenario} does not state its own reason")
        self.assertEqual(outcome, payload["outcome"])
        self.assertEqual(status, payload["exit_status"])
        return written

    def test_every_failure_family_states_a_reason_code_a_phase_and_the_sentence_the_operator_read(self):
        for scenario in FAILURE_FAMILIES:
            with self.subTest(scenario=scenario):
                written = self.failed(scenario)
                for line in written.lines:
                    self.assertEqual([], demo_contracts.validate("event", line), json.dumps(line)[:400])
                record = written.finished()
                payload = record["payload"]
                self.assertEqual(payload["reason_code"], record["reason_code"], "the envelope and the payload disagree about the reason")
                self.assertEqual(written.run["transitions"][-1]["state"], payload["phase"],
                                 "the phase is not the state the controller's own transition record ends in")
                sentence = written.ending_sentence()
                detail = payload["failure"]["detail"]
                self.assertEqual(sentence, detail if payload["exit_status"] == 5 else f"step aborted: {detail}",
                                 "the browser would tell the operator something other than what the terminal said")
                self.assertTrue(payload["failure"]["measurements"] or payload["exit_status"] == 4,
                                "a failure this module raised itself must carry the values that decided it")
                for item in payload["failure"]["measurements"]:
                    self.assertEqual([], demo_contracts.validate("common#/$defs/named_value", item), json.dumps(item))

    def test_a_run_that_ended_as_it_should_carries_no_reason_and_no_failure(self):
        written = EventRun(self)
        payload = written.finished()["payload"]
        self.assertEqual("placed_new", payload["outcome"])
        self.assertIsNone(payload["reason_code"], "a placement is not a failure and must not carry one")
        self.assertIsNone(payload["failure"])
        self.assertIsNone(written.finished()["reason_code"])
        self.assertEqual(written.run["transitions"][-1]["state"], payload["phase"])

    def test_a_refusal_from_before_any_motion_states_the_code_the_preflight_refused_on(self):
        """The other end of the same question: a run that never reached the cycle has no state to be in, so the phase is
        null and says so, and the reason is the code the preflight itself produced rather than a re-reading of its text."""
        os.unlink(os.path.join(self.directory, "calibration", "scan-home-current.json"))
        written = EventRun(self)
        self.assertEqual(("returned", 3), written.run["ending"], written.run["stdout"][-2000:])
        payload = written.finished()["payload"]
        self.assertEqual("refused", payload["outcome"])
        self.assertEqual("saved_pose_missing", payload["reason_code"])
        self.assertIsNone(payload["phase"], "nothing had reached a state of the machine yet")
        self.assertEqual(written.ending_sentence(), payload["failure"]["detail"])
        self.assertEqual([], written.run["sent"], "a refusal before any motion commands nothing")
        self.assertEqual(["saved_pose_missing"], [line["reason_code"] for line in written.of_kind("action.refused")])

    def test_the_pre_pickup_feasibility_refusal_states_the_predicate_its_post_grasp_twin_states(self):
        """THE gap this closes. A cube the cycle could not place is refused at SEARCH, before anything is picked up, and
        that refusal used to leave no predicate at all while its post-grasp twin left one. Both are now the same
        predicate, evaluated at the two moments, and the pre-pickup one names what it rested on and what it measured."""
        written = self.failed("a cube this cycle could not place, decided before the pickup")
        self.assertEqual([], written.run["sent"], "a cube that cannot be placed is left exactly where it lies")
        evaluated = written.predicates("placement_plan")
        self.assertEqual(1, len(evaluated), "the run failed at SEARCH, so there is exactly one placement_plan evaluation")
        self.assertIs(False, evaluated[0]["payload"]["value"])
        self.assertEqual(cycle.PLACE_REQUIRED_CENTRE_MM, evaluated[0]["payload"]["threshold"])
        self.assertEqual("mm", evaluated[0]["payload"]["unit"])
        frames = [line["observation_id"] for line in written.of_kind("observation.captured") if line["payload"]["media"] == "frame"]
        self.assertEqual(1, len(frames), "the cycle observed once and stopped")
        self.assertEqual(frames, written.evidence(evaluated[0], "anchor"), "the predicate does not rest on the observation the code measured the cube in")
        self.assertEqual(frames, written.finished()["payload"]["failure"]["observation_ids"])
        # the numbers, recomputed here from the module's own functions over the scene the world was given
        anchor, direction, grasp_out = differential.WORLD_SCENARIOS["a cube this cycle could not place, decided before the pickup"]["cube_start_pad_m"], (0.0, 1.0), 11.4
        best = max(cycle.release_margin_worst_mm(anchor, direction, float(travel)) for travel in range(0, int(cycle.ARM_LOADED_REACH_OUT_MM - grasp_out) + 1))
        best = max([best] + [cycle.release_margin_worst_mm((anchor[0], anchor[1] + advance / 1000.0), direction, 0.0) for advance in range(0, int(cycle.BASE_MAX_TOTAL_ADVANCE_MM) + 1)])
        self.assertAlmostEqual(best, evaluated[0]["payload"]["measured"], delta=0.01, msg="the stated best achievable margin is not what this scene allows")
        self.assertLess(evaluated[0]["payload"]["measured"], evaluated[0]["payload"]["threshold"], "a refusal whose measurement meets its own threshold")
        carried = evaluated[0]["payload"]["measurements"]
        self.assertEqual(direction, (written.measurement(carried, "reach_direction_pad_x"), written.measurement(carried, "reach_direction_pad_y")))
        self.assertEqual(cycle.BASE_MAX_TOTAL_ADVANCE_MM, written.measurement(carried, "bounded_base_advance_limit_mm"))
        self.assertEqual(cycle.PLACE_REQUIRED_CENTRE_MM, written.measurement(carried, "required_centre_margin_mm"))
        self.assertAlmostEqual(cycle.placed_margin_mm(anchor), written.measurement(carried, "anchor_margin_mm"), delta=0.01)
        self.assertEqual(grasp_out, written.measurement(carried, "grasp_out_mm"))
        # and it really is the twin: the run that places states the SAME predicate, from the other moment
        self.assertTrue(EventRun(self).predicates("placement_plan"), "the post-grasp twin is not stated under this name")

    def test_a_grasp_that_missed_three_times_names_the_three_readbacks_and_nothing_it_did_not_look_at(self):
        written = self.failed("three empty closes at a reach that admits both advances")
        closes = written.predicates("closed_on_something")
        self.assertEqual(3, len(closes), "the bounded recovery closes three times before it gives up")
        self.assertEqual([False, False, False], [record["payload"]["value"] for record in closes])
        for record in closes:
            self.assertEqual([], [item for entry in record["payload"]["evidence"] for item in entry["observation_ids"]], "a gripper readback is not seen in any frame")
        failure = written.finished()["payload"]["failure"]
        self.assertEqual(closes[-1]["payload"]["measured"], written.measurement(failure["measurements"], "gripper"), "the failure does not carry the reading the last close produced")
        self.assertEqual(cycle.GRIPPER_LATCH, written.measurement(failure["measurements"], "gripper_latch"))
        self.assertEqual(3, written.measurement(failure["measurements"], "closes"))

    def test_a_cube_that_did_not_rise_with_the_jaws_names_the_two_frames_the_score_was_taken_between(self):
        written = self.failed("a cube that does not rise with the jaws")
        secured = written.predicates("object_secured")[-1]
        self.assertIs(False, secured["payload"]["value"])
        frames = written.frames_by_action()
        self.assertEqual(frames[written.action_labelled("CLEARANCE")] + frames[written.action_labelled("CONFIRM")], written.evidence(secured, "frame_pair"))
        failure = written.finished()["payload"]["failure"]
        self.assertEqual(secured["payload"]["measured"], written.measurement(failure["measurements"], "retention_score"))
        self.assertEqual(cycle.RETENTION_MIN_SCORE, written.measurement(failure["measurements"], "retention_min_score"))
        self.assertLess(written.measurement(failure["measurements"], "retention_score"), cycle.RETENTION_MIN_SCORE)
        self.assertIs(False, written.finished()["payload"]["predicates"]["object_secured"])

    def test_a_release_the_evidence_does_not_admit_says_which_half_of_the_admission_refused(self):
        written = self.failed("a release the evidence does not admit")
        self.assertIs(False, written.predicates("release_admitted")[-1]["payload"]["value"])
        self.assertIs(False, written.predicates("release_margin")[-1]["payload"]["value"], "the margin is the half that refused in this scene")
        self.assertIs(True, written.predicates("release_height")[-1]["payload"]["value"])
        failure = written.finished()["payload"]["failure"]
        self.assertEqual(written.predicates("release_margin")[-1]["payload"]["measured"], written.measurement(failure["measurements"], "worst_case_centre_margin_mm"))
        self.assertEqual(cycle.PLACE_REQUIRED_CENTRE_MM, written.measurement(failure["measurements"], "required_centre_margin_mm"))
        self.assertEqual(cycle.RELEASE_MAX_HEIGHT_MM, written.measurement(failure["measurements"], "max_release_height_mm"))

    def test_a_base_pulse_the_pad_frames_do_not_measure_names_both_pad_frames_as_its_evidence(self):
        written = self.failed("a base pulse the two pad frames do not measure")
        measured = written.predicates("base_measured")[-1]
        self.assertIs(False, measured["payload"]["value"])
        pair = written.evidence(measured, "pad_measurement")
        self.assertEqual(2, len(pair), "a displacement is measured between exactly two pad frames")
        self.assertEqual(pair, written.finished()["payload"]["failure"]["observation_ids"], "the failure does not name the two frames the refusal rests on")
        failure = written.finished()["payload"]["failure"]
        self.assertEqual(cycle.BASE_RATE_BOUNDS_MM_S[1], written.measurement(failure["measurements"], "max_rate_mm_s"))
        advance, duration = written.measurement(failure["measurements"], "advance_mm"), written.measurement(failure["measurements"], "duration_s")
        self.assertGreater(advance, cycle.BASE_RATE_BOUNDS_MM_S[1] * duration + cycle.BASE_PLAUSIBLE_SLACK_MM, "the stated numbers do not break the bound the code refused on")

    def test_an_operator_abort_on_the_base_path_is_a_stop_and_still_carries_where_the_base_ended_up(self):
        written = self.failed("an operator abort during a base pulse")
        self.assertEqual(["stop.requested", "stop.acknowledged", "stop.outcome"], [line["kind"] for line in written.lines if line["kind"].startswith("stop.")])
        self.assertEqual(1, written.finished()["payload"]["task_control_interventions"])
        failure = written.finished()["payload"]["failure"]
        self.assertEqual(written.measurement(failure["measurements"], "advance_mm"), written.measurement(failure["measurements"], "base_ahead_mm"),
                         "one pulse was measured, so what it advanced IS how far the base ended up from its start")
        self.assertIn(f"{written.measurement(failure['measurements'], 'base_ahead_mm'):+.1f} mm", written.run["stdout"], "the record and the terminal disagree about where the base is")

    def test_a_step_refused_in_the_middle_of_the_cycle_keeps_the_refusal_the_executor_gave(self):
        written = self.failed("jaws that do not reopen after an empty close")
        refused = [line for line in written.of_kind("action.refused") if line["payload"]["action_type"] == "arm_pose"]
        self.assertTrue(refused, "the arm step that was refused is not stated as a refused action")
        self.assertIn(refused[-1]["payload"]["detail"], written.finished()["payload"]["failure"]["detail"], "the cycle's failure does not carry the refusal that caused it")
        self.assertIsNot(True, written.finished()["payload"]["predicates"]["placed"])
        self.assertIsNone(written.finished()["payload"]["predicates"]["object_secured"], "nothing was ever held, so that predicate has no value")


class ReasonCodeTest(EventCase):
    """Where a reason code comes FROM. Every failure exit of this controller mints its own at the raise site; nothing reads
    one back out of a sentence afterwards. The one exception is stated in the source and is not this module's ending: the
    executor (scan_world) has no codes and may not be edited, so its abort sentence is what names it."""

    def source(self) -> str:
        """The controller and the stage policy modules its run_cycle hands the Cycle to (as ctx): one controller, one set of
        raise sites, wherever a stage's procedure lives (docs/autonomous-demo/overnight-plan-2026-09-26.md, track P0a)."""
        manipulation = os.path.join(ROOT, "autonomy", "manipulation")
        paths = [os.path.join(ROOT, "bin", "laptop", "manipulation", "run_single_cube_cycle.py")]
        paths += [os.path.join(manipulation, name) for name in sorted(os.listdir(manipulation)) if name == "stage_policy.py" or (name.startswith("policy_") and name.endswith(".py"))]
        texts = []
        for path in paths:
            with open(path) as handle:
                texts.append(handle.read())
        return "\n".join(texts)

    def test_every_raise_site_mints_its_own_code_and_no_two_sites_share_one(self):
        source = self.source()
        codes = re.findall(r"raise (?:self|cycle|ctx)\.failed\(\"([a-z0-9_]+)\"", source)
        # the one site whose code is chosen at run time: the base-pulse gate has two halves and says which of them refused
        halves = re.findall(r"code, refusal(?:, home)? = \"([a-z0-9_]+)\"", source)
        self.assertEqual(["base_pulse_refused_gripper", "base_pulse_refused_posture"], halves, "the base-pulse gate no longer says which half refused")
        codes += halves
        self.assertGreaterEqual(len(codes), 20, "the cycle has about two dozen failure exits; they cannot all have gone")
        self.assertEqual(sorted(codes), sorted(set(codes)), "two raise sites of one controller share a reason code")
        plain = re.findall(r"raise CycleFailed\(", source)
        self.assertEqual(1, len(plain), "a CycleFailed is raised without a reason code somewhere other than the pure function both callers re-raise")
        self.assertIn("A pure function, so it carries no reason code of its own", source, "the one exception is no longer explained where it is")
        for code, _status, _outcome in FAILURE_FAMILIES.values():
            if code != "operator_abort":  # the executor's, not this module's
                self.assertIn(code, codes, f"{code} is not minted by any raise site of the controller")

    def test_a_code_is_not_a_slice_of_the_sentence_it_travels_with(self):
        """The sentences are English and the codes are identifiers: no code can be recovered from its own message, so a
        reader of the record is reading what the CODE decided, not a re-parse of what it printed."""
        for scenario, (code, _status, _outcome) in FAILURE_FAMILIES.items():
            with self.subTest(scenario=scenario):
                written = EventRun(self, scenario)
                payload = written.finished()["payload"]
                self.assertEqual(code, payload["reason_code"])
                self.assertNotIn(code, payload["failure"]["detail"], "the code appears verbatim in the sentence: this test cannot tell the two apart")
                self.assertEqual([], demo_contracts.validate("common#/$defs/reason_code", payload["reason_code"]))


class StopTest(EventCase):
    def test_an_operator_abort_is_requested_then_acknowledged_from_normal_control_flow(self):
        written = EventRun(self, "an operator abort while an arm segment runs")
        self.assertEqual(("returned", 4), written.run["ending"], written.run["stdout"][-2000:])
        stops = [line for line in written.lines if line["kind"].startswith("stop.")]
        self.assertEqual(["stop.requested", "stop.acknowledged", "stop.outcome"], [line["kind"] for line in stops], "the stop was not stated in the order it happened")
        self.assertEqual("operator_signal", stops[0]["payload"]["origin"])
        self.assertEqual("cycle-controller", stops[1]["payload"]["acknowledged_by"])
        self.assertTrue(stops[1]["reason_code"].startswith("stop_observed_on_"), "the acknowledgement must say where normal flow observed the flag")
        self.assertIsNotNone(stops[1]["payload"]["latency_ns"], "the handler stored when it ran, so the acknowledgement can say how long it took")
        self.assertGreaterEqual(stops[1]["payload"]["latency_ns"], 0)
        self.assertEqual(1, written.of_kind("run.finished")[0]["payload"]["task_control_interventions"])
        self.assertEqual("stopped", written.of_kind("run.finished")[0]["payload"]["outcome"])

    def test_the_signal_handler_itself_never_emits(self):
        """The emitter's queue lock is not re-entrant: a handler that interrupted emit() and then emitted would deadlock
        with the arm moving. The handler is therefore only allowed to write the abort dict, which is what is checked here."""
        written = EventRun(self, "an operator abort while an arm segment runs")
        self.assertTrue(written.lines)
        requested = next(line for line in written.lines if line["kind"] == "stop.requested")
        commanded = [line for line in written.lines if line["kind"] == "action.started"]
        self.assertTrue(any(line["seq"] < requested["seq"] for line in commanded), "the abort arrived before anything moved: the scenario did not exercise a running segment")
        with open(os.path.join(ROOT, "bin", "laptop", "manipulation", "run_single_cube_cycle.py")) as handle:
            installed = [line for line in handle.read().splitlines() if "signal.signal(signal.SIGINT" in line]
        self.assertEqual(1, len(installed), "there is more than one SIGINT handler to check")
        self.assertNotIn("events", installed[0], "the installed handler reaches the events object")
        self.assertNotIn("emit", installed[0], "the installed handler emits")
        self.assertIn("cycle.abort", installed[0], "the handler no longer writes the abort dict the executors poll")


class BrokenEmitterTest(EventCase):
    def test_a_record_that_cannot_be_written_costs_evidence_and_nothing_else(self):
        """The file is opened by the writer thread, so a path that cannot be opened is not a refusal at start: the run goes
        on exactly as it would have, and what is lost is the record, which then says so about itself."""
        captured = {}
        original = cycle.start_events

        def watched(args):
            captured["events"] = original(args)
            return captured["events"]

        unwritable = os.path.join(self.directory, "a-directory-that-does-not-exist", "controller-events.jsonl")
        with mock.patch.object(cycle, "start_events", watched):
            broken = differential.run_scripted_world(cycle, differential.World(), self.directory, configured=True, events_path=unwritable)
        healthy = differential.run_scripted_world(cycle, differential.World(), self.directory, configured=True, events_path=os.path.join(self.directory, "controller-events.jsonl"))
        self.assertEqual(healthy["stdout"], broken["stdout"], "a record that could not be written changed what the operator saw")
        self.assertEqual(healthy["ending"], broken["ending"], "a record that could not be written changed the exit status")
        self.assertEqual(("returned", 0), broken["ending"])
        self.assertFalse(os.path.exists(unwritable), "the events file was created after all: this test proved nothing")
        status = captured["events"].emitter.status()
        self.assertIs(False, status["evidence_complete"], "nothing could be written, so the evidence of this run is not complete")
        self.assertGreater(status["write_errors"], 0)
        self.assertEqual(0, captured["events"].hook_errors, "a hook failed; the writer being broken must not reach the hooks")

    def test_a_hook_that_raises_is_counted_and_swallowed(self):
        """The guarantee the wrapper makes, exercised by breaking one hook on purpose: the run is unchanged and the record
        says an event hook could not state its fact."""
        healthy = differential.run_scripted_world(cycle, differential.World(), self.directory, configured=True, events_path=os.path.join(self.directory, "healthy.jsonl"))
        captured = {}
        original = cycle.start_events

        def watched(args):
            events = original(args)
            captured["events"] = events
            events.predicate = lambda *a, **k: events._hook_failed("predicate")  # noqa: SLF001 -- the wrapper's own accounting is what is under test
            return events

        path = os.path.join(self.directory, "broken-hook.jsonl")
        with mock.patch.object(cycle, "start_events", watched):
            broken = differential.run_scripted_world(cycle, differential.World(), self.directory, configured=True, events_path=path)
        self.assertEqual(healthy["stdout"], broken["stdout"], "a broken hook changed what the operator saw")
        self.assertEqual(healthy["ending"], broken["ending"], "a broken hook changed the exit status")
        self.assertEqual(healthy["sent"], broken["sent"], "a broken hook changed what was commanded")
        with open(path) as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        gaps = [line for line in lines if line["kind"] == "evidence.gap" and line["reason_code"] == "event_hook_failed"]
        self.assertEqual(1, len(gaps), "one cause is one gap, however many times it repeats")
        self.assertEqual("other", gaps[0]["payload"]["gap"])
        self.assertGreater(captured["events"].hook_errors, 1, "the hook was broken for the whole run, so it failed more than once")
        self.assertIs(False, lines[-1]["payload"]["evidence_complete"], "a run that could not state some of its facts is not complete evidence")


class ThePlacementEnvelopeRecordFields(EventCase):
    """T6 step 1b: the two record fields the projected rule is re-sized from go into the events predicates' measurements
    under EVERY envelope word (the default one included), and into the states only under a word: the differential test
    pins the default path's transition record and stdout."""

    def test_release_margin_carries_the_side_margin_after_the_advance_and_base_returned_the_accumulated_lateral(self):
        written = EventRun(self)
        self.assertEqual(("returned", 0), written.run["ending"], written.run["stdout"][-2000:])
        released = written.predicates("release_margin")[-1]
        side = written.measurement(released["payload"]["measurements"], "side_margin_after_advance_mm")
        anchor = released["payload"]  # the moved anchor of the World's base-advance scenario stays on x 0.0145: 193 mm from either side edge
        self.assertAlmostEqual(207.5 - 14.5, side, delta=0.5, msg=str(anchor))
        returned = written.predicates("base_returned")[-1]
        aside = written.measurement(returned["payload"]["measurements"], "base_aside_mm")
        self.assertIsInstance(aside, float)
        measured = [line["payload"] for line in written.predicates("base_measured") if line["payload"]["value"]]
        self.assertGreaterEqual(len(measured), 2, "the base advanced and returned: at least two measured pulses")
        self.assertNotIn("side_margin_after_advance_mm", written.run["stdout"], "under the default word no state gains a field")
        self.assertNotIn("base_aside_mm", written.run["stdout"])
        for line in written.lines:
            self.assertEqual([], demo_contracts.validate("event", line), json.dumps(line)[:400])


if __name__ == "__main__":
    unittest.main()
