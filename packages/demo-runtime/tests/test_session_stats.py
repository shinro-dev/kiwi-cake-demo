#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_session_stats.py: what the runtime computes for itself out of the events it appends.
# Two scopes that must never be mixed: the session counters, which are never reset, and the figures of ONE run, which start
# again when a run starts. A timing figure is per span AND per clock domain, because a duration measured on the robot and a
# duration measured on this machine are not the same measurement. No process, no socket and no file here.
# Run directly: python3 packages/demo-runtime/tests/test_session_stats.py
import sys
import threading
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import read_models as rm  # noqa: E402
from demo_runtime import journal as jn  # noqa: E402

import demo_contracts  # noqa: E402

FIXED_CAKE = {"artifacts": False, "resident": False, "admin_socket": False}


def event(kind, payload, *, seq, run_id=None):
    record = jn.make_event(kind, payload, source_session="rt-test-0001", run_id=run_id)
    record["seq"] = seq
    return record


def span(name, duration_ns, *, seq, run_id, clock_domain="laptop_monotonic"):
    return event("timing.span", {"span": name, "duration_ns": duration_ns, "clock_domain": clock_domain}, seq=seq, run_id=run_id)


def started(run_id, *, seq):
    return event("run.started", {"command_id": "c1", "command_type": "start_cycle", "data_mode": "synthetic", "plan_only": False, "holding": None}, seq=seq, run_id=run_id)


def finished(run_id, *, seq, complete=True, gaps=0):
    payload = {"outcome": "placed_new", "exit_status": 0, "predicates": {"object_secured": True, "placed": True, "clearance_target_met": True, "base_returned": True},
               "automatic_retries": 0, "task_control_interventions": 0, "evidence_complete": complete, "evidence_gaps": gaps}
    return event("run.finished", payload, seq=seq, run_id=run_id)


def gap(run_id, *, seq):
    return event("evidence.gap", {"gap": "frame_missing", "detail": "the frame was not written", "observation_ids": []}, seq=seq, run_id=run_id)


def feed(stats, records):
    for record in records:
        stats.observe(record)
    return stats


def snapshot_of(stats):
    return rm.build_snapshot(runtime_session="rt-test-0001", seq_cursor=9, retained_from_seq=1, data_mode="synthetic",
                             link={"state": "unknown", "source_stamp": None, "receive_age_ms": None}, owner={"kind": "free", "run_id": None, "lease": None},
                             active_run=None, last_outcome=None, capability_list=rm.capabilities(block_code=None, run_active=False, replay_only=False),
                             component_list=rm.components(stats=stats, run_active=False, last_exit=None, replay_only=False, runtime_version="0.1.0"),
                             cake=rm.cake_view(dict(FIXED_CAKE)), evidence=stats.evidence(), timings=stats.timings(), run_evidence=stats.run_evidence(),
                             latest_retained_observation=stats.latest_retained_observation(),
                             latest_measured_state=stats.latest_measured_state())


def captured(observation_id, *, seq, retained=True, media="frame", role="wrist", run_id=None):
    payload = {"source_role": role, "media": media, "width_px": 1280, "height_px": 720,
               "content_digest": "sha256:4d1f2a9c8b3e", "source_stamp": None, "retained": retained}
    record = event("observation.captured", payload, seq=seq, run_id=run_id)
    record["observation_id"] = observation_id
    return record


STAMP = {"session": "rt-host-0001", "seq": 41, "sample_mono_ns": 2_000_000_000, "clock_domain": "pi_monotonic"}


def measured(pan, *, seq, run_id=None, stamp=STAMP):
    payload = {"joints": {"shoulder_pan": pan, "shoulder_lift": -79.429, "elbow_flex": 38.374,
                          "wrist_flex": 67.209, "wrist_roll": -96.659, "gripper": 79.848},
               "base_velocity": {"x_mps": 0.0, "y_mps": 0.0, "theta_dps": 0.0},
               "consumed_source_stamp": stamp}
    return event("measured.state", payload, seq=seq, run_id=run_id)


class TheNewestControllerMeasurement(unittest.TestCase):
    """The hole the frame reference closed, in a second place. A controller writes one measured.state record when it
    reads the robot, and a non-motion observe writes one as much as a cycle does. A console folds those off the event
    stream, so a tab that connected or reloaded afterwards holds none and reported that nothing was measuring, naming
    the absent telemetry tap as the reason, while the whole pose sat in this session's log. These cases are what the
    snapshot may and may not say about it."""

    def test_a_session_that_measured_nothing_says_so_with_none_and_never_with_an_empty_object(self):
        stats = rm.SessionStats()
        self.assertIsNone(stats.latest_measured_state())
        self.assertIsNone(snapshot_of(stats)["latest_measured_state"])

    def test_the_newest_measurement_is_carried_with_the_fields_the_record_states(self):
        stats = feed(rm.SessionStats(), [measured(-1.0, seq=3), measured(-9.582, seq=5)])
        entry = snapshot_of(stats)["latest_measured_state"]
        self.assertEqual(entry["seq"], 5)
        self.assertEqual(entry["joints"]["shoulder_pan"], -9.582)
        self.assertEqual(entry["base_velocity"], {"x_mps": 0.0, "y_mps": 0.0, "theta_dps": 0.0})
        self.assertEqual(entry["consumed_source_stamp"], STAMP)
        self.assertIsInstance(entry["t_wall_utc"], str, "the wall time the runtime wrote on the record, carried unaltered")
        self.assertEqual(demo_contracts.validate("snapshot", snapshot_of(stats)), [])

    def test_an_observe_that_moved_nothing_still_leaves_a_measurement_on_the_snapshot(self):
        # The run that produced the real defect: one observe, no motion, outcome unknown, every placement predicate
        # null, and a full pose recorded. The snapshot has to carry that pose, or a reloaded tab has nothing at all.
        stats = feed(rm.SessionStats(), [started("run-observe", seq=1), measured(-9.582, seq=5, run_id="run-observe")])
        self.assertEqual(snapshot_of(stats)["latest_measured_state"]["joints"]["shoulder_pan"], -9.582)

    def test_a_record_missing_a_field_of_the_pose_is_not_remembered_at_all(self):
        # A partial pose is not a pose anybody measured, and half a reading on the screen is worse than none.
        record = measured(-9.582, seq=5)
        del record["payload"]["base_velocity"]
        self.assertIsNone(feed(rm.SessionStats(), [record]).latest_measured_state())

    def test_the_answer_is_a_copy_so_a_reader_cannot_reach_into_the_runtime_s_own_record(self):
        stats = feed(rm.SessionStats(), [measured(-9.582, seq=5)])
        taken = stats.latest_measured_state()
        taken["seq"] = 999
        self.assertEqual(stats.latest_measured_state()["seq"], 5)


class TheNewestRetainedFrame(unittest.TestCase):
    """A console folds its observations off the event stream, so a tab that connects or reloads AFTER a capture has
    folded none of them and nothing in its state names a frame. That is how a running console came to tell an operator
    that the controller had stored no frame while the frame was on disk and the API was serving it. The snapshot now
    carries the reference, and these cases are what it may and may not say."""

    def test_a_session_that_captured_nothing_says_so_with_none_and_never_with_an_empty_object(self):
        stats = rm.SessionStats()
        self.assertIsNone(stats.latest_retained_observation())
        self.assertIsNone(snapshot_of(stats)["latest_retained_observation"])

    def test_the_newest_retained_frame_is_carried_with_the_fields_the_record_states(self):
        stats = feed(rm.SessionStats(), [captured("obs-0001", seq=4), captured("obs-0007", seq=17)])
        entry = snapshot_of(stats)["latest_retained_observation"]
        self.assertEqual(entry["observation_id"], "obs-0007")
        self.assertEqual(entry["seq"], 17)
        self.assertEqual((entry["source_role"], entry["media"]), ("wrist", "frame"))
        self.assertEqual((entry["width_px"], entry["height_px"]), (1280, 720))
        self.assertEqual(entry["content_digest"], "sha256:4d1f2a9c8b3e")
        self.assertTrue(entry["retained"])
        self.assertIsInstance(entry["t_wall_utc"], str, "the wall time the runtime wrote on the record, carried unaltered")
        self.assertEqual(demo_contracts.validate("snapshot", snapshot_of(stats)), [])

    def test_a_capture_that_retained_nothing_never_displaces_the_frame_that_can_be_served(self):
        # The only use of this field is to name a picture somebody can fetch. A capture with no frame kept has none.
        stats = feed(rm.SessionStats(), [captured("obs-0007", seq=17), captured("obs-0008", seq=21, retained=False)])
        self.assertEqual(snapshot_of(stats)["latest_retained_observation"]["observation_id"], "obs-0007")

    def test_a_capture_with_no_picture_in_it_never_displaces_one_that_has(self):
        stats = feed(rm.SessionStats(), [captured("obs-0007", seq=17), captured("obs-0009", seq=22, media="scalar_set")])
        self.assertEqual(snapshot_of(stats)["latest_retained_observation"]["observation_id"], "obs-0007")

    def test_a_record_that_names_no_observation_is_not_remembered_at_all(self):
        # Nobody could fetch it, and an entry naming nothing would put the card back to drawing an empty box.
        record = captured("obs-0007", seq=17)
        record["observation_id"] = None
        self.assertIsNone(feed(rm.SessionStats(), [record]).latest_retained_observation())

    def test_the_answer_is_a_copy_so_a_reader_cannot_reach_into_the_runtime_s_own_record(self):
        stats = feed(rm.SessionStats(), [captured("obs-0007", seq=17)])
        taken = stats.latest_retained_observation()
        taken["observation_id"] = "obs-changed-by-the-reader"
        self.assertEqual(stats.latest_retained_observation()["observation_id"], "obs-0007")


class Timings(unittest.TestCase):
    def test_nothing_is_reported_before_a_sample_exists(self):
        stats = rm.SessionStats()
        self.assertEqual(stats.timings(), {"run_id": None, "spans": []}, "an empty list says nothing was measured; it is not a zero")
        feed(stats, [started("console-1", seq=1)])
        self.assertEqual(stats.timings(), {"run_id": "console-1", "spans": []})

    def test_two_clock_domains_are_never_pooled_into_one_figure(self):
        stats = feed(rm.SessionStats(), [started("console-1", seq=1),
                                         span("capture", 100, seq=2, run_id="console-1"),
                                         span("capture", 300, seq=3, run_id="console-1", clock_domain="pi_monotonic"),
                                         span("capture", 200, seq=4, run_id="console-1")])
        figures = {(figure["span"], figure["clock_domain"]): figure for figure in stats.timings()["spans"]}
        self.assertEqual(sorted(figures), [("capture", "laptop_monotonic"), ("capture", "pi_monotonic")])
        laptop = figures[("capture", "laptop_monotonic")]
        self.assertEqual((laptop["count"], laptop["total_ns"], laptop["max_ns"], laptop["last_ns"]), (2, 300, 200, 200))
        self.assertEqual((laptop["first_seq"], laptop["last_seq"]), (2, 4), "every figure carries the window it was taken over")
        self.assertEqual(figures[("capture", "pi_monotonic")]["count"], 1)

    def test_a_new_run_starts_its_own_figures_and_does_not_inherit_the_last_ones(self):
        stats = feed(rm.SessionStats(), [started("console-1", seq=1), span("motion", 500, seq=2, run_id="console-1"), finished("console-1", seq=3),
                                         started("console-2", seq=4), span("motion", 700, seq=5, run_id="console-2")])
        self.assertEqual(stats.timings()["run_id"], "console-2")
        self.assertEqual([(figure["span"], figure["count"], figure["total_ns"]) for figure in stats.timings()["spans"]], [("motion", 1, 700)])

    def test_a_span_that_belongs_to_no_run_is_not_attributed_to_one(self):
        stats = feed(rm.SessionStats(), [span("waiting", 90, seq=1, run_id=None), started("console-1", seq=2), span("waiting", 90, seq=3, run_id="console-9")])
        self.assertEqual(stats.timings(), {"run_id": "console-1", "spans": []}, "a span of another run is not folded into this one")

    def test_a_duration_that_is_not_a_whole_number_of_nanoseconds_is_not_counted(self):
        # The contract requires an integer, so this cannot arrive through EventLog.append; observe() still refuses it
        # rather than putting a float, a bool or a string into a figure a console prints as nanoseconds.
        stats = feed(rm.SessionStats(), [started("console-1", seq=1)])
        for bad in (True, 1.5, "90", None):
            stats.observe(event("timing.span", {"span": "settle", "duration_ns": bad, "clock_domain": "laptop_monotonic"}, seq=2, run_id="console-1"))
        self.assertEqual(stats.timings()["spans"], [])


class RunEvidence(unittest.TestCase):
    def test_a_session_that_recorded_nothing_states_no_run_at_all(self):
        stats = rm.SessionStats()
        self.assertIsNone(stats.run_evidence(), "an absence of gaps in a session with no record is not a mission that went well")
        self.assertEqual(stats.evidence(), {"complete": True, "gaps": 0, "dropped_telemetry": 0}, "the session counters keep their own meaning")

    def test_completeness_stays_unknown_until_the_run_itself_states_it(self):
        stats = feed(rm.SessionStats(), [started("console-1", seq=1), gap("console-1", seq=2)])
        self.assertEqual(stats.run_evidence(), {"run_id": "console-1", "records": 2, "gaps": 1, "dropped_telemetry": 0, "complete": None})
        feed(stats, [finished("console-1", seq=3, complete=False, gaps=1)])
        self.assertEqual(stats.run_evidence()["complete"], False, "the run's own run.finished record is what says it")

    def test_the_run_counters_and_the_session_counters_answer_different_questions(self):
        stats = feed(rm.SessionStats(), [started("console-1", seq=1), gap("console-1", seq=2), finished("console-1", seq=3, complete=False, gaps=1),
                                         started("console-2", seq=4), finished("console-2", seq=5)])
        self.assertEqual(stats.evidence(), {"complete": False, "gaps": 1, "dropped_telemetry": 0}, "the session is never reset by a new run")
        self.assertEqual(stats.run_evidence(), {"run_id": "console-2", "records": 2, "gaps": 0, "dropped_telemetry": 0, "complete": True})

    def test_dropped_telemetry_is_counted_for_the_run_as_well_as_for_the_session(self):
        dropped = event("telemetry.dropped", {"stage": "emitter_queue", "count": 4, "window_ms": 1000}, seq=2, run_id="console-1")
        stats = feed(rm.SessionStats(), [started("console-1", seq=1), dropped])
        self.assertEqual((stats.dropped_telemetry, stats.run_evidence()["dropped_telemetry"]), (4, 4))


class ReadWhileWriting(unittest.TestCase):
    def test_the_figures_are_read_while_events_are_still_being_appended(self):
        # observe() runs on the thread that appends to the event log; timings() runs on the thread that answers a
        # snapshot. Iterating a dict another thread is growing raises instead of answering, and a run that starts while
        # a figure is being read replaces the whole table, so the two are driven at once here rather than in turn.
        stats = feed(rm.SessionStats(), [started("console-1", seq=1)])
        halt, failures = threading.Event(), []

        def append():
            names, domains, seq = ("capture", "inference", "motion", "settle"), ("laptop_monotonic", "pi_monotonic"), 2
            while not halt.is_set():
                if seq % 97 == 0:
                    stats.observe(started(f"console-{seq}", seq=seq))
                stats.observe(span(names[seq % 4], 10 + seq, seq=seq, run_id=stats.run["run_id"], clock_domain=domains[seq % 2]))
                seq += 1

        writer = threading.Thread(target=append, name="appending")
        writer.start()
        try:
            for _ in range(3000):
                try:
                    for figure in stats.timings()["spans"]:
                        self.assertGreaterEqual(figure["total_ns"], figure["max_ns"], "a figure is never read half written")
                    stats.run_evidence()
                except RuntimeError as error:  # "dictionary changed size during iteration" and its relatives
                    failures.append(repr(error))
        finally:
            halt.set()
            writer.join(timeout=10)
        self.assertEqual(failures, [])


class TheSnapshotTheyGoInto(unittest.TestCase):
    def test_a_snapshot_with_the_figures_and_the_run_evidence_in_it_validates(self):
        stats = feed(rm.SessionStats(), [started("console-1", seq=1), span("inference", 95_000_000, seq=2, run_id="console-1"), gap("console-1", seq=3)])
        snapshot = snapshot_of(stats)
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [], "the whole answer validates: nothing has to be stripped first")
        self.assertEqual(snapshot["timings"]["spans"][0]["span"], "inference")
        self.assertEqual(snapshot["run_evidence"]["run_id"], "console-1")

    def test_a_snapshot_of_a_runtime_that_has_seen_nothing_validates_too(self):
        snapshot = snapshot_of(rm.SessionStats())
        self.assertEqual(demo_contracts.validate("snapshot", snapshot), [])
        self.assertEqual((snapshot["timings"], snapshot["run_evidence"]), ({"run_id": None, "spans": []}, None))


class ACapabilityBlockedByTheRobotAndNotByThisLaptop(unittest.TestCase):
    """A command capability carries WHY it is off, and a fact about the robot is not the same kind of fact as a flag on
    this laptop. Four of these codes are answers the robot computer gave, so the capability is blocked and names which;
    two of them mean this runtime has no answer at all, which is 'unknown' and must not be drawn as ready either."""

    def commands(self, block_code, detail):
        served = rm.capabilities(block_code=block_code, block_detail=detail, run_active=False, replay_only=False)
        return [capability for capability in served if capability["id"] in rm.COMMAND_CAPABILITIES]

    def test_the_answers_the_robot_gave_block_every_command_and_each_one_states_the_cause(self):
        for code in ("motor_host_unreachable", "motor_host_not_running", "motor_host_in_scan_mode", "motor_host_mode_unknown"):
            with self.subTest(code=code):
                for capability in self.commands(code, "the sentence the reading came with"):
                    self.assertEqual(("blocked", code, "the sentence the reading came with", "robot"),
                                     (capability["state"], capability["reason_code"], capability["reason"], capability["dependency"]))
                    self.assertEqual(demo_contracts.validate("capability", capability), [])

    def test_a_reading_this_runtime_does_not_have_is_unknown_and_never_enabled(self):
        for code in ("robot_readiness_not_read", "robot_readiness_stale"):
            with self.subTest(code=code):
                for capability in self.commands(code, "nothing has been read from the robot computer yet"):
                    self.assertEqual(("unknown", code), (capability["state"], capability["reason_code"]))
                    self.assertNotEqual("enabled", capability["state"], "staleness is not readiness")
                    self.assertEqual(demo_contracts.validate("capability", capability), [])

    def test_stop_is_never_taken_away_by_a_robot_that_is_not_ready(self):
        served = rm.capabilities(block_code="motor_host_not_running", block_detail="no host", run_active=True, replay_only=False)
        stop = next(capability for capability in served if capability["id"] == "stop")
        self.assertEqual(("enabled", None), (stop["state"], stop["reason_code"]), "a run that is going must always be stoppable")

    def test_the_codes_this_table_recognizes_are_the_ones_the_runtime_really_writes(self):
        """The table mirrors demo_runtime.preflight. A code renamed there and not here would quietly fall through to the
        preflight default and be shown as a fault of this laptop instead of a fact about the robot."""
        from demo_runtime import preflight

        written = {preflight._READINESS_REASONS[state][0] for state in preflight.READINESS_STATES if state != preflight.ROBOT_READY}
        self.assertEqual(written, set(rm.ROBOT_BLOCK_STATES))
        self.assertEqual({"blocked", "unknown"}, set(rm.ROBOT_BLOCK_STATES.values()))


if __name__ == "__main__":
    unittest.main()
