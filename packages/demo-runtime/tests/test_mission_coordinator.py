# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""The two-cube mission, end to end, with no resident, no robot and no browser.

This drives the whole sequence the rehearsal performs: the reachable target completes, the second one waits and says
why, a reload reads the same mission and target back without starting anything, an invalid activation is refused and
leaves the mission waiting, a valid one is observed, and the same target resumes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from demo_runtime import cake_activation, mission, mission_coordinator  # noqa: E402
from demo_runtime.mission_coordinator import (CapabilityAdapter, ExecutionAdapter,  # noqa: E402
                                              MissionCancelled, MissionCoordinator)
from demo_runtime.mission_errors import HardwareMappingUnavailable  # noqa: E402

TARGETS = [
    {"target_id": "cube_near", "label": "the reachable cube", "reachable": True, "requires_capability": False},
    {"target_id": "cube_far", "label": "the far cube", "reachable": False, "requires_capability": True},
]


def recording_execution() -> ExecutionAdapter:
    """An adapter that performs NO motion and says so. It is a rehearsal double, never evidence about hardware."""
    return ExecutionAdapter(
        read_pose=lambda: {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
        read_base=lambda: {"vx_mps": 0.0, "vy_mps": 0.0, "omega_rps": 0.0},
        move_arm=lambda target, duration_s, speed: {"ok": True},
        move_base_sustained=lambda vx, duration_s: True,
        confirm=lambda prompt: "y",
        performs_motion=False,
        label="recording-rehearsal",
    )


class FakeCandidates:
    def __init__(self):
        self._bytes = {"mobile_approach.activate": b"the prepared swapped candidate"}

    def payload(self, key):
        return self._bytes.get(key)

    def digest(self, key):
        payload = self._bytes.get(key)
        return None if payload is None else cake_activation.digest_of(payload)

    def problem(self, key):
        return None if key in self._bytes else "candidate_unreadable"


class ScriptedCapability:
    """A capability slot whose answers are scripted, so the coordinator's ORDER is what is under test."""

    def __init__(self, answers, *, before_submit=None):
        self.available_flag = False
        self.answers = list(answers)
        self.submitted = []
        self.candidates = FakeCandidates()
        # A submit that blocks until a test lets it go, which is what a resident taking its time looks like from
        # here. It is how the cases about a stop landing mid-activation are driven.
        self._before_submit = before_submit

    def adapter(self):
        return CapabilityAdapter(available=lambda: self.available_flag, submit=self._submit,
                                 observe=lambda: self.available_flag, candidates=self.candidates)

    def _submit(self, operation_uuid, payload):
        if self._before_submit is not None:
            self._before_submit()
        self.submitted.append((operation_uuid, payload))
        answer = self.answers.pop(0)
        if answer.get("_activates"):
            self.available_flag = True
        return answer


ACCEPTED_ANSWER = {"outcome": cake_activation.ACCEPTED, "transaction_phase": "COMPLETED",
                   "transaction_uuid": "t1", "detail": "the transaction completed", "_activates": True}
REJECTED_ANSWER = {"outcome": cake_activation.REJECTED_BY_CAKE, "transaction_phase": "ABORTED",
                   "transaction_uuid": "t0", "detail": cake_activation.REJECTION_DETAIL}
REQUEST_FAILED_ANSWER = {"outcome": cake_activation.REQUEST_FAILED, "transaction_phase": None,
                         "transaction_uuid": None, "detail": "operation_identity_mismatch"}


class TheMissionSequence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, mission.MISSIONS_FILE_NAME)

    def coordinator(self, capability, **kwargs):
        made = MissionCoordinator(self.path, recording_execution(), capability.adapter(),
                                  data_mode="synthetic", run_target=_placed_when_available, **kwargs)
        self.addCleanup(made.close)
        return made

    def wait_until_waiting(self, coordinator, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = coordinator.state()
            if state and state["state"] == mission.MISSION_WAITING:
                return state
            time.sleep(0.02)
        self.fail("the mission never reached WAITING_FOR_CAPABILITY")

    def test_the_first_target_completes_and_the_second_waits_and_says_why(self):
        capability = ScriptedCapability([])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        state = self.wait_until_waiting(coordinator)
        self.assertEqual(state["state"], mission.MISSION_WAITING)
        self.assertEqual(state["selected_target"], "cube_far")
        self.assertEqual(state["waiting_reason"], mission.WAITING_ON_CAPABILITY)
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_PLACED)
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_WAITING)
        coordinator.stop()

    def test_an_auto_target_waits_at_the_gate_like_a_capability_target(self):
        """The gate reads the slot for a target whose flag is "auto" exactly as for True: this closure is reached for an
        auto target only after its run measured BASE_ADVANCE (mission_execution), and a rehearsal's machine asks it always."""
        capability = ScriptedCapability([ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability)
        coordinator.start([{"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": mission.REQUIRES_CAPABILITY_AUTO}])
        state = self.wait_until_waiting(coordinator)
        self.assertEqual((state["selected_target"], state["waiting_reason"]), ("cube", mission.WAITING_ON_CAPABILITY))
        self.assertEqual(state["targets"][0]["requires_capability"], "auto", "the record carries the word as it was declared")
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertTrue(coordinator.join(5.0))
        self.assertEqual(coordinator.state()["state"], mission.MISSION_COMPLETE)

    def test_a_reload_reads_the_same_mission_and_target_back_and_starts_nothing(self):
        capability = ScriptedCapability([])
        coordinator = self.coordinator(capability)
        started = coordinator.start(TARGETS)
        waiting = self.wait_until_waiting(coordinator)
        coordinator.stop()
        coordinator.join(2.0)

        # A SECOND COORDINATOR OVER THE SAME FILE: this is what a reload is. It reads; it submits nothing.
        reloaded = self.coordinator(ScriptedCapability([]))
        recovered = reloaded.recover()
        self.assertEqual(recovered["mission_id"], started["mission_id"])
        self.assertEqual(recovered["selected_target"], waiting["selected_target"])
        self.assertEqual(recovered["waiting_reason"], mission.WAITING_ON_CAPABILITY)
        self.assertFalse(reloaded.running)
        by_id = {target["target_id"]: target for target in recovered["targets"]}
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_PLACED)

    def test_a_rejected_activation_leaves_the_mission_waiting_and_is_not_called_a_request_failure(self):
        capability = ScriptedCapability([REJECTED_ANSWER])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_REJECTED)
        self.assertEqual(answer["transaction_phase"], "ABORTED")
        self.assertEqual(answer["detail"], cake_activation.REJECTION_DETAIL)
        state = coordinator.state()
        self.assertEqual(state["state"], mission.MISSION_WAITING)
        self.assertEqual(state["selected_target"], "cube_far")
        self.assertEqual(len(state["activations"]), 1)
        coordinator.stop()

    def test_a_request_failure_is_not_recorded_as_cake_rejecting_the_candidate(self):
        capability = ScriptedCapability([REQUEST_FAILED_ANSWER])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_FAILED)
        self.assertNotEqual(answer["activation_state"], mission.ACTIVATION_REJECTED)
        coordinator.stop()

    def test_a_valid_activation_is_observed_and_the_same_target_resumes(self):
        capability = ScriptedCapability([REJECTED_ANSWER, ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability)
        started = coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        self.assertEqual(coordinator.submit_activation("mobile_approach.activate")["activation_state"],
                         mission.ACTIVATION_REJECTED)
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertTrue(coordinator.join(5.0), "the mission did not finish after the capability became available")
        state = coordinator.state()
        self.assertEqual(state["mission_id"], started["mission_id"])
        self.assertEqual(state["state"], mission.MISSION_COMPLETE)
        by_id = {target["target_id"]: target for target in state["targets"]}
        # THE SAME TARGET RESUMED. Not the next one, and not a new mission.
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_PLACED)
        self.assertEqual(len(state["activations"]), 2)
        self.assertEqual([entry["activation_state"] for entry in state["activations"]],
                         [mission.ACTIVATION_REJECTED, mission.ACTIVATION_OBSERVED])

    def test_a_completed_transaction_whose_slot_did_not_move_is_not_an_activation(self):
        """`status ok` and a COMPLETED phase still are not the activation. Only the slot read back is.

        CHANGED DELIBERATELY: this used to pin SUBMITTED, which also meant "in flight", so an operation still running
        and one that completed without moving the slot read the same. It now has its own state, and SUBMITTED means in
        flight only. What the test protects is unchanged: it is not OBSERVED."""
        capability = ScriptedCapability([dict(ACCEPTED_ANSWER, _activates=False)])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(answer["outcome"], cake_activation.ACCEPTED)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED)
        self.assertNotEqual(answer["activation_state"], mission.ACTIVATION_SUBMITTED, "SUBMITTED is in flight, and this is not")
        self.assertNotEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        coordinator.stop()

    def test_the_activation_binds_the_operation_uuid_to_the_bytes_before_submitting(self):
        capability = ScriptedCapability([ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        answer = coordinator.submit_activation("mobile_approach.activate")
        submitted_uuid, submitted_bytes = capability.submitted[0]
        self.assertEqual(submitted_uuid, answer["operation_uuid"])
        entry = coordinator.state()["activations"][0]
        self.assertEqual(entry["operation_uuid"], submitted_uuid)
        self.assertEqual(entry["payload_digest"], cake_activation.digest_of(submitted_bytes))
        self.assertEqual(entry["candidate_key"], "mobile_approach.activate")
        coordinator.join(5.0)

    def test_an_unprepared_candidate_key_is_refused_and_nothing_is_submitted(self):
        capability = ScriptedCapability([])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        answer = coordinator.submit_activation("something.else")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "unknown_candidate")
        self.assertEqual(capability.submitted, [])
        coordinator.stop()

    def test_an_activation_is_refused_when_no_mission_is_waiting(self):
        capability = ScriptedCapability([ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability)
        self.assertEqual(coordinator.submit_activation("mobile_approach.activate")["reason_code"], "unknown_mission")
        capability.available_flag = True
        coordinator.start(TARGETS)
        self.assertTrue(coordinator.join(5.0))
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "mission_not_waiting_for_capability")
        self.assertEqual(capability.submitted, [])

    def test_a_stop_is_answered_while_a_target_is_waiting(self):
        capability = ScriptedCapability([])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        started = time.monotonic()
        coordinator.stop()
        self.assertTrue(coordinator.join(5.0), "a stop asked for while waiting was not answered")
        # Responsive means within a poll or so, not at the end of the mission.
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(coordinator.state()["state"], mission.MISSION_STOPPED)

    def test_a_late_activation_answer_is_recorded_and_does_not_resume_a_stopped_mission(self):
        """The stop lands WHILE the activation is in flight, and the answer that arrives afterwards succeeds.

        Two things have to hold, and they are not the same thing. The answer is on the record, because the slot
        really did move and a record that said otherwise would say less than happened. And the target does not
        resume: a mission that was stopped stays stopped, whatever the slot now carries.
        """
        entered, released = threading.Event(), threading.Event()

        def in_flight() -> None:
            entered.set()
            released.wait(5.0)

        capability = ScriptedCapability([ACCEPTED_ANSWER], before_submit=in_flight)
        coordinator = self.coordinator(capability)
        started = coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)

        answers: list = []
        submitting = threading.Thread(target=lambda: answers.append(coordinator.submit_activation("mobile_approach.activate")))
        submitting.start()
        self.addCleanup(submitting.join)
        # The stop is asked for once the submit really is inside the capability adapter, which is what "in flight"
        # means: the attempt is already on the record and the bytes are already with the resident.
        self.assertTrue(entered.wait(5.0), "the activation never reached the capability adapter")
        coordinator.stop()
        self.assertTrue(coordinator.join(5.0), "the stop was not answered while the activation was in flight")
        released.set()
        submitting.join(5.0)

        answer = answers[0]
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED, "the slot did move and that is recorded")
        self.assertTrue(answer["stopped_during_activation"], "the caller is told the mission it was for is stopping")
        state = coordinator.state()
        self.assertEqual(state["mission_id"], started["mission_id"])
        self.assertEqual(state["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(state["activations"]), 1)
        self.assertEqual(state["activations"][0]["activation_state"], mission.ACTIVATION_OBSERVED)
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertNotEqual(by_id["cube_far"]["state"], mission.TARGET_PLACED, "a stopped mission must not resume")
        self.assertFalse(coordinator.running)

    def test_a_capability_that_arrives_after_a_stop_does_not_let_a_target_leave_the_gate(self):
        """The same rule from the waiting thread's side, with no activation involved at all. A slot that becomes
        available at the moment a stop is asked for must not carry the target."""
        capability = ScriptedCapability([])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        coordinator.stop()
        capability.available_flag = True  # the capability arrives, late
        self.assertTrue(coordinator.join(5.0))
        state = coordinator.state()
        self.assertEqual(state["state"], mission.MISSION_STOPPED)
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertNotEqual(by_id["cube_far"]["state"], mission.TARGET_PLACED)

    def test_a_second_mission_is_refused_while_one_is_running(self):
        capability = ScriptedCapability([])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        second = coordinator.start(TARGETS)
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason_code"], "mission_already_running")
        coordinator.stop()

    def test_child_run_outcomes_stay_with_their_own_target(self):
        capability = ScriptedCapability([ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_until_waiting(coordinator)
        coordinator.submit_activation("mobile_approach.activate")
        self.assertTrue(coordinator.join(5.0))
        by_id = {target["target_id"]: target for target in coordinator.state()["targets"]}
        self.assertEqual(by_id["cube_near"]["runs"], ["run-cube_near"])
        self.assertEqual(by_id["cube_far"]["runs"], ["run-cube_far"])

    def test_the_record_says_whether_the_adapter_performed_motion(self):
        """A rehearsal outcome must not be readable as a physical one."""
        self.assertFalse(recording_execution().performs_motion)
        self.assertEqual(recording_execution().label, "recording-rehearsal")


class AStopIsPerMissionAndIsNoticedAtEveryStep(unittest.TestCase):
    """Each mission has its own cancel event, every machine step refuses once it is set, and a stop that finds nothing
    carrying the mission ends it itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, mission.MISSIONS_FILE_NAME)

    def coordinator(self, capability, execution=None, run_target=None):
        made = MissionCoordinator(self.path, execution or recording_execution(), capability.adapter(),
                                  data_mode="synthetic", run_target=run_target or _placed_when_available)
        self.addCleanup(made.close)
        return made

    def wait_for_state(self, coordinator, predicate, what, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = coordinator.state()
            if state and predicate(state):
                return state
            time.sleep(0.02)
        self.fail(f"timed out waiting for {what}")

    def test_a_stop_during_the_first_target_is_noticed_before_its_next_machine_step_and_the_far_target_is_never_selected(self):
        """The stop lands while a step of the FIRST target is running. That step is not interrupted (nothing here can
        do that); the NEXT step refuses, so the adapter is never asked for it, and the second target is never selected."""
        entered, released = threading.Event(), threading.Event()
        calls: list = []

        def read_pose():
            calls.append("read_pose")
            entered.set()
            released.wait(5.0)
            return {}

        def read_base():
            calls.append("read_base")
            return {}

        execution = ExecutionAdapter(read_pose=read_pose, read_base=read_base,
                                     move_arm=lambda *args: calls.append("move_arm"),
                                     move_base_sustained=lambda *args: calls.append("move_base_sustained"),
                                     confirm=lambda prompt: calls.append("confirm"),
                                     performs_motion=False, label="recording-rehearsal")

        def two_steps(execution, capability_available, wait_for_capability_change, target):
            execution.read_pose()
            execution.read_base()
            return {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": f"run-{target['target_id']}"}

        coordinator = self.coordinator(ScriptedCapability([]), execution=execution, run_target=two_steps)
        coordinator.start(TARGETS)
        self.assertTrue(entered.wait(5.0), "the first step never started")
        self.assertTrue(coordinator.stop()["stopping"])
        released.set()
        self.assertTrue(coordinator.join(5.0))
        self.assertEqual(calls, ["read_pose"], "the step after the stop was refused before the adapter was asked for it")
        state = coordinator.state()
        self.assertEqual(state["state"], mission.MISSION_STOPPED)
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_STOPPED, "a stopped mission's active target reads STOPPED, not ACTIVE")
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_PENDING, "the far target was never selected")
        self.assertNotEqual(state["selected_target"], "cube_far")

    def test_each_machine_step_refuses_after_a_stop_and_is_otherwise_the_adapter_s_own(self):
        cancel = threading.Event()
        seen: list = []
        execution = ExecutionAdapter(read_pose=lambda: seen.append("read_pose") or {"p": 1},
                                     read_base=lambda: seen.append("read_base") or {"b": 2},
                                     move_arm=lambda target, duration_s, speed: seen.append("move_arm") or {"m": 3},
                                     move_base_sustained=lambda vx, duration_s: seen.append("move_base_sustained") or True,
                                     confirm=lambda prompt: seen.append("confirm") or "YES",
                                     performs_motion=False, label="recording-rehearsal",
                                     log=lambda *args, **kwargs: None, attach_cancel=lambda event: None)
        guarded = mission_coordinator.refusing_after_stop(execution, cancel)
        self.assertEqual((guarded.read_pose(), guarded.read_base(), guarded.move_arm({}, 1.0, 0.0),
                          guarded.move_base_sustained(0.1, 1.0), guarded.confirm("?")),
                         ({"p": 1}, {"b": 2}, {"m": 3}, True, "YES"), "before a stop every answer is the adapter's own")
        self.assertIs(guarded.log, execution.log, "the log sink is passed through, never turned into a control path")
        self.assertIs(guarded.attach_cancel, execution.attach_cancel)
        cancel.set()
        seen.clear()
        for name, args in (("read_pose", ()), ("read_base", ()), ("move_arm", ({}, 1.0, 0.0)),
                           ("move_base_sustained", (0.1, 1.0)), ("confirm", ("?",))):
            with self.subTest(step=name):
                with self.assertRaises(MissionCancelled):
                    getattr(guarded, name)(*args)
        self.assertEqual(seen, [], "no step reached the adapter after the stop")

    def test_an_answer_for_a_stopped_mission_stays_that_mission_s_after_another_one_starts(self):
        """Mission A is stopped while its activation is in flight; mission B starts and waits; then A's answer lands.

        With ONE shared event B's start cleared A's stop, so A's answer said it had not been stopped. Each mission has
        its own event now: A's answer is recorded against A with stopped_during_activation true, and nothing about B
        changes because of it."""
        entered, released = threading.Event(), threading.Event()

        def in_flight() -> None:
            entered.set()
            released.wait(5.0)

        # The transaction completes but the slot does not move, so B has nothing to leave its own gate on: whatever B
        # does next is not A's answer resuming it.
        capability = ScriptedCapability([dict(ACCEPTED_ANSWER, _activates=False)], before_submit=in_flight)
        coordinator = self.coordinator(capability)
        first = coordinator.start(TARGETS)
        self.wait_for_state(coordinator, lambda state: state["state"] == mission.MISSION_WAITING, "A to wait")
        answers: list = []
        submitting = threading.Thread(target=lambda: answers.append(coordinator.submit_activation("mobile_approach.activate")))
        submitting.start()
        self.addCleanup(submitting.join)
        self.assertTrue(entered.wait(5.0))
        coordinator.stop()
        self.assertTrue(coordinator.join(5.0))
        second = coordinator.start(TARGETS)
        self.assertTrue(second["ok"])
        self.wait_for_state(coordinator, lambda state: state["mission_id"] == second["mission_id"] and state["state"] == mission.MISSION_WAITING,
                            "B to wait")
        released.set()
        submitting.join(5.0)

        answer = answers[0]
        self.assertEqual(answer["mission_id"], first["mission_id"])
        self.assertTrue(answer["stopped_during_activation"], "A was stopped, whatever B did since")
        a_state = coordinator.state(first["mission_id"])
        self.assertEqual(a_state["state"], mission.MISSION_STOPPED)
        self.assertEqual([entry["operation_uuid"] for entry in a_state["activations"]], [answer["operation_uuid"]])
        self.assertIs(a_state["activations"][0]["stopped_during_activation"], True, "and the record says so, durably")
        b_state = coordinator.state(second["mission_id"])
        self.assertEqual(b_state["activations"], [], "A's answer is not B's")
        self.assertEqual(b_state["state"], mission.MISSION_WAITING, "and it did not move B")
        self.assertFalse(b_state["stop_requested"])
        coordinator.stop()

    def test_a_stop_before_the_submit_is_answered_mission_stopping_and_nothing_is_written_or_submitted(self):
        capability = ScriptedCapability([ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability)
        coordinator.start(TARGETS)
        self.wait_for_state(coordinator, lambda state: state["state"] == mission.MISSION_WAITING, "the mission to wait")
        coordinator.stop()
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, mission_coordinator.MISSION_STOPPING))
        self.assertEqual(capability.submitted, [])
        self.assertTrue(coordinator.join(5.0))
        self.assertEqual(coordinator.state()["activations"], [], "no attempt record was written")
        # And the same answer once the mission has ended STOPPED: the stop is what it is refused for.
        self.assertEqual(coordinator.submit_activation("mobile_approach.activate")["reason_code"], mission_coordinator.MISSION_STOPPING)
        self.assertEqual(capability.submitted, [])

    def test_a_stop_that_lands_during_the_slot_read_before_the_submit_still_writes_nothing(self):
        """The check is asked again after the read that precedes the attempt, because that read can take a while."""
        capability = ScriptedCapability([ACCEPTED_ANSWER])
        coordinator_box: list = []

        def read_slot():
            coordinator_box[0].stop()  # the stop lands while the slot is being read
            return {"read": cake_activation.READ_OK, "capability_active": False}

        adapter = capability.adapter()
        adapter.read_slot = read_slot
        coordinator = MissionCoordinator(self.path, recording_execution(), adapter, data_mode="synthetic",
                                         run_target=_placed_when_available)
        self.addCleanup(coordinator.close)
        coordinator_box.append(coordinator)
        coordinator.start(TARGETS)
        self.wait_for_state(coordinator, lambda state: state["state"] == mission.MISSION_WAITING, "the mission to wait")
        answer = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(answer["reason_code"], mission_coordinator.MISSION_STOPPING)
        self.assertEqual(capability.submitted, [])
        self.assertTrue(coordinator.join(5.0))
        self.assertEqual(coordinator.state()["activations"], [])

    def test_after_a_resume_the_mission_reads_running_and_a_second_activation_is_refused(self):
        """The same target leaves the gate and is being carried. The record says RUNNING with no waiting reason, and an
        operator is not offered, nor allowed, a second activation for a capability that is already there."""
        hold = threading.Event()
        self.addCleanup(hold.set)

        def placed_after_hold(execution, capability_available, wait_for_capability_change, target):
            while not capability_available("mobile_approach"):
                wait_for_capability_change()
            if target["target_id"] == "cube_far":
                hold.wait(5.0)  # being carried: the resume is on the record and the target is not finished
            execution.read_pose()
            return {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": f"run-{target['target_id']}"}

        capability = ScriptedCapability([ACCEPTED_ANSWER, ACCEPTED_ANSWER])
        coordinator = self.coordinator(capability, run_target=placed_after_hold)
        coordinator.start(TARGETS)
        self.wait_for_state(coordinator, lambda state: state["state"] == mission.MISSION_WAITING, "the mission to wait")
        self.assertEqual(coordinator.submit_activation("mobile_approach.activate")["activation_state"], mission.ACTIVATION_OBSERVED)
        state = self.wait_for_state(coordinator, lambda one: one["state"] == mission.MISSION_RUNNING, "the resume to be recorded")
        self.assertIsNone(state["waiting_reason"])
        self.assertEqual(state["selected_target"], "cube_far")
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_ACTIVE)
        second = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual((second["ok"], second["reason_code"]), (False, "mission_not_waiting_for_capability"))
        self.assertEqual(len(capability.submitted), 1, "nothing was submitted a second time")
        hold.set()
        self.assertTrue(coordinator.join(5.0))
        self.assertEqual(coordinator.state()["state"], mission.MISSION_COMPLETE)

    def test_a_stop_with_nothing_to_stop_says_so_and_stops_nothing(self):
        coordinator = self.coordinator(ScriptedCapability([]))
        empty = coordinator.stop()
        self.assertEqual((empty["ok"], empty["stopping"], empty["reason_code"]), (True, False, mission_coordinator.NO_RUNNING_MISSION))
        capability = ScriptedCapability([])
        capability.available_flag = True
        finished = self.coordinator(capability)
        started = finished.start(TARGETS)
        self.assertTrue(finished.join(5.0))
        after = finished.stop()
        self.assertEqual((after["mission_id"], after["stopping"], after["reason_code"], after["state"]),
                         (started["mission_id"], False, mission_coordinator.NO_RUNNING_MISSION, mission.MISSION_COMPLETE))
        self.assertFalse(finished.state()["stop_requested"], "a finished mission gets no stop request written against it")

    def test_a_mission_nothing_is_carrying_is_ended_stopped_by_the_stop_itself(self):
        """The runtime that carried the mission died while it waited. No thread will ever notice an event, so the stop
        writes the end, and says why."""
        from demo_runtime.journal import Journal

        journal = Journal(self.path)
        for record in (mission.mission_started("mission-orphan0001", TARGETS, t_wall_utc="2026-09-23T00:00:00Z", data_mode="synthetic",
                                               performs_motion=False, execution_label="recording-rehearsal"),
                       mission.target_selected("mission-orphan0001", "cube_near", t_wall_utc="2026-09-23T00:00:01Z"),
                       mission.target_outcome("mission-orphan0001", "cube_near", mission.TARGET_PLACED, t_wall_utc="2026-09-23T00:00:02Z"),
                       mission.target_selected("mission-orphan0001", "cube_far", t_wall_utc="2026-09-23T00:00:03Z"),
                       mission.target_waiting("mission-orphan0001", "cube_far", mission.WAITING_ON_CAPABILITY, t_wall_utc="2026-09-23T00:00:04Z")):
            journal.append(record)
        journal.close()

        coordinator = self.coordinator(ScriptedCapability([]))
        self.assertEqual(coordinator.state()["state"], mission.MISSION_WAITING)
        answer = coordinator.stop()
        self.assertEqual((answer["mission_id"], answer["stopping"], answer["carried"], answer["state"]),
                         ("mission-orphan0001", True, False, mission.MISSION_STOPPED))
        state = coordinator.state()
        self.assertEqual(state["state"], mission.MISSION_STOPPED)
        self.assertEqual(state["detail"], mission_coordinator.ORPHAN_STOP_DETAIL)
        self.assertTrue(state["stop_requested"])
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_STOPPED)
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_PLACED)
        self.assertFalse(coordinator.stop()["stopping"], "and a second stop has nothing left to stop")

    def test_a_hardware_refusal_is_recorded_failed_with_its_name_and_never_as_an_operator_abort(self):
        def refuses(target, duration_s, speed):
            raise HardwareMappingUnavailable("no physical mapping for the requested wrist move")

        execution = ExecutionAdapter(read_pose=lambda: {}, read_base=lambda: {}, move_arm=refuses,
                                     move_base_sustained=lambda vx, duration_s: True, confirm=lambda prompt: "YES",
                                     performs_motion=True, label="robot-adapter-under-test")

        def one_move(execution, capability_available, wait_for_capability_change, target):
            execution.move_arm({"wrist_roll": 1.0}, 1.0, 0.0)
            return {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": None}

        coordinator = self.coordinator(ScriptedCapability([]), execution=execution, run_target=one_move)
        coordinator.start(TARGETS)
        self.assertTrue(coordinator.join(5.0))
        state = coordinator.state()
        self.assertEqual(state["state"], mission.MISSION_FAILED)
        self.assertEqual(state["detail"], "HardwareMappingUnavailable: no physical mapping for the requested wrist move")
        self.assertNotIn("ABORT", state["detail"].upper())
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_FAILED)
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_PENDING)

    def test_the_log_seam_gets_the_machine_s_events_and_attach_cancel_gets_this_mission_s_event(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        if not os.path.isdir(os.path.join(root, "autonomy", "manipulation")):
            raise unittest.SkipTest("autonomy is not in this tree")
        import dataclasses

        from demo_runtime import mission_rehearsal

        rehearsal, _transcript = mission_rehearsal.recording_execution(mode="synthetic")
        logged: list = []
        attached: list = []
        execution = dataclasses.replace(rehearsal, log=lambda event, **fields: logged.append(event),
                                        attach_cancel=attached.append)
        coordinator = MissionCoordinator(self.path, execution, ScriptedCapability([]).adapter(), data_mode="synthetic")
        self.addCleanup(coordinator.close)
        first = coordinator.start([TARGETS[0]])
        self.assertTrue(coordinator.join(10.0))
        self.assertEqual(coordinator.state()["state"], mission.MISSION_COMPLETE)
        self.assertIn("STATE_ENTER", logged, "the machine's own events reached the adapter's sink")
        self.assertEqual(logged[-1], "DONE")
        second = coordinator.start([TARGETS[0]])
        self.assertTrue(coordinator.join(10.0))
        self.assertEqual(len(attached), 2, "once per mission, before its targets")
        self.assertTrue(all(isinstance(event, threading.Event) for event in attached))
        self.assertIsNot(attached[0], attached[1], "each mission's own event, never a shared one")
        self.assertNotEqual(first["mission_id"], second["mission_id"])


class TheActivationKeepsItsEvidence(unittest.TestCase):
    """The transaction and the slot read back are recorded, not only a boolean; the read that decides is the one
    recorded; SUBMITTED means in flight and nothing else."""

    SLOT = "505054304050806000016d6f62696c65"
    INERT = "96" * 32
    MOBILE = "2f" * 32

    def listing(self, capsule: str, generation: int, epoch: int) -> str:
        return "\n".join(["status ok", "replay 0", "snapshot_present 1", "session_uuid " + "01" * 16,
                          "plan_digest " + ("ee" if epoch > 2 else "8b") * 32, f"epoch {epoch}",
                          f"slot capsule={capsule} config=cc generation={generation} instance={6003861253238292480 + generation} "
                          f"name=mobile_approach state=ACTIVE uuid={self.SLOT}", ""])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, mission.MISSIONS_FILE_NAME)

    def run_one(self, answer, *, moves: bool, observe_says=None):
        capability = ScriptedCapability([answer])
        listings = {"now": self.listing(self.INERT, 1, 2)}

        def submit(operation_uuid, payload):
            capability.submitted.append((operation_uuid, payload))
            if moves:
                listings["now"] = self.listing(self.MOBILE, 2, 3)
            return capability.answers.pop(0)

        def read_slot():
            return cake_activation.slot_reading(listings["now"], slot_uuid=self.SLOT, active_capsule=self.MOBILE)

        adapter = CapabilityAdapter(available=lambda: read_slot()["capability_active"], submit=submit,
                                    observe=(lambda: observe_says) if observe_says is not None else (lambda: read_slot()["capability_active"]),
                                    candidates=capability.candidates, read_slot=read_slot)
        coordinator = MissionCoordinator(self.path, recording_execution(), adapter, data_mode="synthetic",
                                         run_target=_placed_when_available)
        self.addCleanup(coordinator.close)
        coordinator.start(TARGETS)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (coordinator.state() or {}).get("state") != mission.MISSION_WAITING:
            time.sleep(0.02)
        result = coordinator.submit_activation("mobile_approach.activate")
        coordinator.stop()
        coordinator.join(5.0)
        return result, coordinator.state()["activations"][0]

    def test_the_transaction_and_the_slot_before_and_after_are_on_the_record(self):
        answer = dict(ACCEPTED_ANSWER, status="ok", transaction_handle="7", epoch=None)
        result, entry = self.run_one(answer, moves=True)
        self.assertEqual(result["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertEqual(entry["activation_state"], mission.ACTIVATION_OBSERVED)
        transaction = entry["transaction"]
        self.assertEqual((transaction["operation_uuid"], transaction["transaction_uuid"], transaction["transaction_phase"],
                          transaction["status"], transaction["transaction_handle"]),
                         (entry["operation_uuid"], "t1", "COMPLETED", "ok", "7"))
        before, after = entry["slot_before"], entry["slot_after"]
        self.assertEqual((before["slot_uuid"], before["name"], before["state"], before["capsule"], before["generation"]),
                         (self.SLOT, "mobile_approach", "ACTIVE", self.INERT, "1"))
        self.assertEqual((after["capsule"], after["generation"], after["instance"]), (self.MOBILE, "2", str(6003861253238292482)))
        self.assertEqual((before["session_uuid"], after["session_uuid"]), ("01" * 16, "01" * 16), "the same resident answered both reads")
        self.assertEqual((before["epoch"], after["epoch"]), ("2", "3"))
        self.assertEqual((before["plan_digest"], after["plan_digest"]), ("8b" * 32, "ee" * 32))
        self.assertTrue(after["capability_active"])
        self.assertFalse(before["capability_active"])
        self.assertIs(entry["stopped_during_activation"], False)

    def test_the_recorded_read_is_the_one_that_decides(self):
        """observe() would say True; the slot read that is recorded says the capability is not there. The record and
        the verdict come from the same read, so the verdict is COMPLETED_NOT_OBSERVED."""
        result, entry = self.run_one(dict(ACCEPTED_ANSWER), moves=False, observe_says=True)
        self.assertEqual(result["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED)
        self.assertEqual(entry["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED)
        self.assertEqual(entry["slot_after"]["generation"], "1")
        self.assertFalse(entry["slot_after"]["capability_active"])

    def test_a_transaction_still_running_is_submitted_which_means_in_flight(self):
        running = {"outcome": cake_activation.PENDING, "transaction_phase": "RUNNING", "transaction_uuid": "t9",
                   "detail": "RUNNING", "status": "ok"}
        result, entry = self.run_one(running, moves=False)
        self.assertEqual(result["activation_state"], mission.ACTIVATION_SUBMITTED)
        self.assertEqual(entry["activation_state"], mission.ACTIVATION_SUBMITTED)
        self.assertNotEqual(entry["activation_state"], mission.ACTIVATION_FAILED, "a running transaction is not a request that failed")

    def test_a_slot_reading_without_its_verdict_still_gets_the_answer_recorded(self):
        """A read_slot that answers a dict with no capability_active is a reading that decides nothing. It comes AFTER
        the submit, so if it raised, the resident's answer to an operation that may have moved the slot would be lost
        and the record would say the operation was still in flight."""
        capability = ScriptedCapability([dict(ACCEPTED_ANSWER)])
        adapter = capability.adapter()
        adapter.read_slot = lambda: {"read": cake_activation.READ_OK}
        coordinator = MissionCoordinator(self.path, recording_execution(), adapter, data_mode="synthetic",
                                         run_target=_placed_when_available)
        self.addCleanup(coordinator.close)
        coordinator.start(TARGETS)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (coordinator.state() or {}).get("state") != mission.MISSION_WAITING:
            time.sleep(0.02)
        result = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual((result["ok"], result["activation_state"]), (True, mission.ACTIVATION_COMPLETED_NOT_OBSERVED))
        self.assertIn("could not be read back", result["detail"], "a reading with no verdict is not a slot seen without the capability")
        coordinator.stop()
        coordinator.join(5.0)
        entry = coordinator.state()["activations"][0]
        self.assertEqual(entry["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED, "the answer record was written")
        self.assertEqual(entry["outcome"], cake_activation.ACCEPTED)


class OneVerdictForEverySurfaceThatSubmits(unittest.TestCase):
    """mission_coordinator.activation_verdict is the lines the mission's activation decided with, moved out unchanged so
    the capability slot check (demo_runtime.capability_check) decides with the same ones. Every case below is the verdict
    the inline lines gave, and the coordinator is shown to reach the shared function rather than a copy of it."""

    OK = {"read": cake_activation.READ_OK, "capability_active": True}
    NOT_THERE = {"read": cake_activation.READ_OK, "capability_active": False}

    def verdict(self, answer, slot_after, observe=None):
        asked = []

        def observing():
            asked.append(True)
            return bool(observe)

        return mission_coordinator.activation_verdict(answer, slot_after, observing), asked

    def test_every_outcome_comes_to_the_verdict_it_always_came_to(self):
        rejected = {"outcome": cake_activation.REJECTED_BY_CAKE, "detail": cake_activation.REJECTION_DETAIL}
        pending = {"outcome": cake_activation.PENDING, "detail": "RUNNING"}
        failed = {"outcome": cake_activation.REQUEST_FAILED, "detail": "admin_probe_timeout"}
        cases = [
            (ACCEPTED_ANSWER, self.OK, None, (mission.ACTIVATION_OBSERVED, ACCEPTED_ANSWER.get("detail"))),
            (ACCEPTED_ANSWER, self.NOT_THERE, True,
             (mission.ACTIVATION_COMPLETED_NOT_OBSERVED, "the transaction completed but the slot does not carry the capability")),
            (ACCEPTED_ANSWER, {"read": cake_activation.READ_FAILED, "capability_active": False}, None,
             (mission.ACTIVATION_COMPLETED_NOT_OBSERVED, "the transaction completed and the slot could not be read back")),
            (ACCEPTED_ANSWER, {"read": cake_activation.READ_OK}, None,
             (mission.ACTIVATION_COMPLETED_NOT_OBSERVED, "the transaction completed and the slot could not be read back")),
            (ACCEPTED_ANSWER, {"read": cake_activation.READ_OK, "capability_active": "yes"}, None,
             (mission.ACTIVATION_COMPLETED_NOT_OBSERVED, "the transaction completed and the slot could not be read back")),
            (rejected, self.OK, True, (mission.ACTIVATION_REJECTED, cake_activation.REJECTION_DETAIL)),
            (pending, self.OK, True, (mission.ACTIVATION_SUBMITTED, "RUNNING")),
            (failed, self.OK, True, (mission.ACTIVATION_FAILED, "admin_probe_timeout")),
            ({}, None, True, (mission.ACTIVATION_FAILED, None)),
        ]
        for answer, slot_after, observe, expected in cases:
            with self.subTest(outcome=answer.get("outcome"), slot=slot_after):
                verdict, asked = self.verdict(answer, slot_after, observe)
                self.assertEqual(verdict, expected)
                self.assertEqual(asked, [], "observe is asked only when there is no reading at all")

    def test_observe_decides_only_without_a_reading_and_is_asked_once(self):
        for says, expected in ((True, mission.ACTIVATION_OBSERVED), (False, mission.ACTIVATION_COMPLETED_NOT_OBSERVED)):
            verdict, asked = self.verdict(ACCEPTED_ANSWER, None, says)
            self.assertEqual((verdict[0], asked), (expected, [True]))

    def test_the_transaction_evidence_is_the_answer_s_own_fields(self):
        answer = {"transaction_uuid": "t1", "transaction_handle": "7", "transaction_phase": "COMPLETED", "status": "ok",
                  "reason_code": None, "epoch": "4", "outcome": cake_activation.ACCEPTED}
        self.assertEqual(mission_coordinator.transaction_evidence("op-1", answer),
                         {"operation_uuid": "op-1", "transaction_uuid": "t1", "transaction_handle": "7", "transaction_phase": "COMPLETED",
                          "status": "ok", "reason_code": None, "answer_epoch": "4"})

    def test_the_coordinator_decides_through_the_shared_function(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        capability = ScriptedCapability([dict(ACCEPTED_ANSWER)])
        coordinator = MissionCoordinator(os.path.join(tmp.name, mission.MISSIONS_FILE_NAME), recording_execution(),
                                         capability.adapter(), data_mode="synthetic", run_target=_placed_when_available)
        self.addCleanup(coordinator.close)
        coordinator.start(TARGETS)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (coordinator.state() or {}).get("state") != mission.MISSION_WAITING:
            time.sleep(0.02)
        calls = []
        real = mission_coordinator.activation_verdict

        def spy(answer, slot_after, observe):
            calls.append(answer.get("outcome"))
            return real(answer, slot_after, observe)

        original = mission_coordinator.activation_verdict
        mission_coordinator.activation_verdict = spy
        try:
            result = coordinator.submit_activation("mobile_approach.activate")
        finally:
            mission_coordinator.activation_verdict = original
        coordinator.stop()
        coordinator.join(5.0)
        self.assertEqual(calls, [cake_activation.ACCEPTED])
        # This scripted slot activates on an accepted answer and reads no slot detail, so observe() decides: OBSERVED.
        self.assertEqual(result["activation_state"], mission.ACTIVATION_OBSERVED)


class OneActivationIsInFlightAtATime(unittest.TestCase):
    """Section 2's retry rules: an operation's state is retrieved, never resubmitted on a guess. A second press while
    the first is still with the resident, or after the resident said the transaction is still running, is a NEW
    operation uuid for the same intent, which is exactly the guess those rules forbid."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, mission.MISSIONS_FILE_NAME)

    def waiting(self, capability) -> MissionCoordinator:
        coordinator = MissionCoordinator(self.path, recording_execution(), capability.adapter(), data_mode="synthetic",
                                         run_target=_placed_when_available)
        self.addCleanup(coordinator.close)
        coordinator.start(TARGETS)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (coordinator.state() or {}).get("state") != mission.MISSION_WAITING:
            time.sleep(0.02)
        self.assertEqual(coordinator.state()["state"], mission.MISSION_WAITING)
        return coordinator

    def test_a_second_activation_while_the_first_is_with_the_resident_is_refused_and_submits_nothing(self):
        entered, release = threading.Event(), threading.Event()

        def held():
            entered.set()
            release.wait(5.0)

        capability = ScriptedCapability([dict(REJECTED_ANSWER), dict(ACCEPTED_ANSWER)], before_submit=held)
        coordinator = self.waiting(capability)
        first: list = []
        worker = threading.Thread(target=lambda: first.append(coordinator.submit_activation("mobile_approach.activate")))
        worker.start()
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(5.0), "the first activation reached the resident")
        second = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual((second["ok"], second["reason_code"]), (False, mission_coordinator.ACTIVATION_IN_FLIGHT))
        self.assertEqual(second["mission_id"], coordinator.state()["mission_id"])
        release.set()
        worker.join(5.0)
        self.assertEqual(len(capability.submitted), 1, "exactly one operation went to the resident")
        self.assertEqual(first[0]["activation_state"], mission.ACTIVATION_REJECTED)
        self.assertEqual(len(coordinator.state()["activations"]), 1, "the refused press wrote no attempt record")
        coordinator.stop()

    def test_an_activation_the_resident_says_is_still_running_refuses_the_next_one(self):
        running = {"outcome": cake_activation.PENDING, "transaction_phase": "RUNNING", "transaction_uuid": "t9",
                   "detail": "RUNNING", "status": "ok"}
        capability = ScriptedCapability([running, dict(ACCEPTED_ANSWER)])
        coordinator = self.waiting(capability)
        first = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual(first["activation_state"], mission.ACTIVATION_SUBMITTED)
        second = coordinator.submit_activation("mobile_approach.activate")
        self.assertEqual((second["ok"], second["reason_code"]), (False, mission_coordinator.ACTIVATION_IN_FLIGHT))
        self.assertEqual(second["operation_uuid"], first["operation_uuid"], "the refusal names the operation still in flight")
        self.assertEqual(len(capability.submitted), 1)
        self.assertEqual([entry["activation_state"] for entry in coordinator.state()["activations"]], [mission.ACTIVATION_SUBMITTED])
        coordinator.stop()

    def test_an_answered_activation_does_not_block_the_next_one(self):
        """The refusal is about an operation IN FLIGHT and nothing wider: a rejection is an answer, and the operator
        may try again with a fresh operation."""
        capability = ScriptedCapability([dict(REJECTED_ANSWER), dict(ACCEPTED_ANSWER)])
        coordinator = self.waiting(capability)
        self.assertEqual(coordinator.submit_activation("mobile_approach.activate")["activation_state"], mission.ACTIVATION_REJECTED)
        second = coordinator.submit_activation("mobile_approach.activate")
        self.assertTrue(second["ok"])
        self.assertEqual(second["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertEqual(len(capability.submitted), 2)
        self.assertNotEqual(capability.submitted[0][0], capability.submitted[1][0], "each attempt is a fresh operation")
        coordinator.stop()


class TheDurableRecord(unittest.TestCase):
    def test_a_record_of_an_unknown_version_is_skipped_and_counted_never_guessed_at(self):
        records = [
            mission.mission_started("m1", TARGETS, t_wall_utc="2026-09-23T00:00:00Z", data_mode="synthetic", performs_motion=False, execution_label="recording-rehearsal"),
            dict(mission.target_selected("m1", "cube_near", t_wall_utc="2026-09-23T00:00:01Z"), v=99),
        ]
        folded = mission.fold_missions(records)
        self.assertIn("m1", folded)
        self.assertIsNone(folded["m1"]["selected_target"])
        self.assertEqual(mission.unknown_version_records(records), 1)

    def test_nothing_after_the_end_changes_it_and_a_late_answer_is_still_recorded(self):
        """STOPPED stays STOPPED. A late answer is folded into the activations; a late resume, a late placement and a
        late COMPLETE are counted and change nothing."""
        mission_id = "mission-sticky000001"
        records = [
            mission.mission_started(mission_id, TARGETS, t_wall_utc="t0", data_mode="synthetic", performs_motion=False, execution_label="recording-rehearsal"),
            mission.target_selected(mission_id, "cube_near", t_wall_utc="t1"),
            mission.target_outcome(mission_id, "cube_near", mission.TARGET_PLACED, run_id="run-near", t_wall_utc="t2"),
            mission.target_selected(mission_id, "cube_far", t_wall_utc="t3"),
            mission.target_waiting(mission_id, "cube_far", mission.WAITING_ON_CAPABILITY, t_wall_utc="t4"),
            mission.activation_attempt(mission_id, "cube_far", "op-1", "k", "sha256:abc", t_wall_utc="t5"),
            mission.mission_stop_requested(mission_id, origin="mission_stop", t_wall_utc="t6"),
            mission.mission_ended(mission_id, mission.MISSION_STOPPED, detail="stopped", t_wall_utc="t7"),
            mission.activation_answer(mission_id, "op-1", mission.ACTIVATION_OBSERVED, outcome="ACCEPTED",
                                      transaction_phase="COMPLETED", stopped_during_activation=True, t_wall_utc="t8"),
            mission.target_resumed(mission_id, "cube_far", t_wall_utc="t9"),
            mission.target_outcome(mission_id, "cube_far", mission.TARGET_PLACED, run_id="run-far", t_wall_utc="t10"),
            mission.mission_ended(mission_id, mission.MISSION_COMPLETE, t_wall_utc="t11"),
        ]
        view = mission.mission_view(mission.fold_missions(records)[mission_id])
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(view["detail"], "stopped")
        by_id = {target["target_id"]: target for target in view["targets"]}
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_STOPPED)
        self.assertEqual(by_id["cube_far"]["runs"], [], "the late run was not attributed")
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_PLACED)
        self.assertEqual(view["activations"][0]["activation_state"], mission.ACTIVATION_OBSERVED, "the late answer is on the record")
        self.assertIs(view["activations"][0]["stopped_during_activation"], True)
        self.assertEqual(view["records_after_end"], 3)
        self.assertTrue(view["stop_requested"])
        self.assertEqual(view["stop_requested_wall_utc"], "t6")

    def test_a_resume_record_clears_the_waiting_reason_and_reads_running(self):
        mission_id = "mission-resume000001"
        records = [
            mission.mission_started(mission_id, TARGETS, t_wall_utc="t0", data_mode="synthetic", performs_motion=False, execution_label="recording-rehearsal"),
            mission.target_selected(mission_id, "cube_far", t_wall_utc="t1"),
            mission.target_waiting(mission_id, "cube_far", mission.WAITING_ON_CAPABILITY, t_wall_utc="t2"),
            mission.target_resumed(mission_id, "cube_far", t_wall_utc="t3"),
        ]
        view = mission.mission_view(mission.fold_missions(records)[mission_id])
        self.assertEqual((view["state"], view["waiting_reason"], view["selected_target"]), (mission.MISSION_RUNNING, None, "cube_far"))
        self.assertEqual({target["target_id"]: target["state"] for target in view["targets"]}["cube_far"], mission.TARGET_ACTIVE)

    def test_the_view_carries_no_path_no_bytes_and_no_motion_command(self):
        records = [mission.mission_started("m1", TARGETS, t_wall_utc="2026-09-23T00:00:00Z", data_mode="synthetic", performs_motion=False, execution_label="recording-rehearsal"),
                   mission.activation_attempt("m1", "cube_far", "u1", "k", "sha256:abc",
                                              t_wall_utc="2026-09-23T00:00:02Z")]
        view = mission.mission_view(mission.fold_missions(records)["m1"])
        text = repr(view)
        for forbidden in ("vx_mps", "omega_rps", ".cakepln", "/run/", "payload\":"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(view["activations"][0]["payload_digest"], "sha256:abc")

    def test_the_mission_record_version_is_its_own_and_moves_on_its_own(self):
        """The two versions happen to be 1 today. What matters is that neither is derived from the other, so this
        pins the mission view to its OWN symbol: a contract bump must not silently renumber these records, and a
        mission bump must not look like a contract change."""
        records = [mission.mission_started("m1", TARGETS, t_wall_utc="2026-09-23T00:00:00Z", data_mode="synthetic", performs_motion=False, execution_label="recording-rehearsal")]
        view = mission.mission_view(mission.fold_missions(records)["m1"])
        self.assertEqual(view["mission_record_version"], mission.MISSION_RECORD_VERSION)
        self.assertEqual(records[0]["v"], mission.MISSION_RECORD_VERSION)
        self.assertNotIn("contract_version", view)
        import inspect
        self.assertNotIn("CONTRACT_VERSION", inspect.getsource(mission))


def _placed_when_available(execution, capability_available, wait_for_capability_change, target):
    """Stands in for the manipulation machine so these cases test the coordinator's ORDER.

    It calls the SAME two capability callables the real transition logic calls, in the same order, so the waiting,
    the recorded reason and the resume are exercised for real. A separate case drives the real machine.
    """
    while not capability_available("mobile_approach"):
        wait_for_capability_change()
    execution.read_pose()
    return {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": f"run-{target['target_id']}"}


class TheRealTransitionLogicIsTheOneThatRuns(unittest.TestCase):
    """The default run_target reaches autonomy's own state machine rather than a copy of it."""

    def test_the_default_target_runner_uses_hybrid_pick_place(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        if not os.path.isdir(os.path.join(root, "autonomy", "manipulation")):
            raise unittest.SkipTest("autonomy is not in this tree")
        from demo_runtime import mission_coordinator
        import inspect
        source = inspect.getsource(mission_coordinator._run_hybrid_target)
        self.assertIn("run_hybrid_pick_place", source)
        self.assertIn("PickPlaceDeps", source)
        # The seam the mission drives is the machine's own, not a reimplementation.
        self.assertIn("capability_available=capability_available", source)
        self.assertIn("wait_for_capability_change=wait_for_capability_change", source)


if __name__ == "__main__":
    unittest.main()
