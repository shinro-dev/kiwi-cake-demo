#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_execution.py: the robot-side execution of a mission, against the FAKE controller
# only (tests/fake_controller.py), through a real synthetic Supervisor. No robot, camera, network, ssh or Cake resident.
#
# What is pinned here: the GRASP_POSITION request the hybrid machine makes and the guard that refuses it with nothing sent;
# the outcome mapping of a supervised run (and the result file's echo); the stop, with a blocking child, during a
# capability wait and, with the real source, before a capability admission; a late ACCEPTED activation that must start
# nothing; that this module never reaches the rehearsal; and THE CAPABILITY decided on the MEASURED mode: no radii, no
# run; a target whose binding measured BASE_ADVANCE fails with no gate and no spawn when no actual observation exists;
# a first-execution capability target is spawned WITHOUT the capability, and only its SEARCH's measurement leads to the
# gate and to one respawn carrying it. The refusal and the post-gate paths are proven with STAND-IN answers (UNAVAILABLE,
# AVAILABLE); ONE test asks the REAL source, target_observation_source.capability_observation_available, and pins what
# follows under the v2 observation contract: it answers available (a mapping exists; the controller decides per run), so
# a bound BASE_ADVANCE target passes the gate and its run is spawned carrying the capability. THE AUTO TARGET
# (requires_capability "auto"): its first execution is spawned WITHOUT the capability; a run that completed ARM_ONLY ends
# the target placed with nothing asked; a run whose SEARCH measured BASE_ADVANCE and refused records the need (source
# search), waits at the coordinator's gate, and the one respawn carries the capability; and a run whose SEARCH bound the
# arm alone with an in-run fallback that moves the base (the controller's additive placement words) and refused records
# the need (source search_fallback) and is respawned in the same way. The fake controller's measured mode for an auto
# target is chosen per scenario by KC_FAKE_AUTO_MODE, and the arm-alone refusal by KC_FAKE_ARM_ALONE_FALLBACK.
# Run directly: python3 packages/demo-runtime/tests/test_mission_execution.py
import ast
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))
sys.path.insert(0, str(REPO_ROOT))

from demo_runtime import cake_activation, mission  # noqa: E402
from demo_runtime import mission_execution as mx  # noqa: E402
from demo_runtime import supervisor as sv  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.journal import EventLog, Journal, read_json_lines  # noqa: E402
from demo_runtime.mission_coordinator import CAPABILITY_POLL_S, CapabilityAdapter, MissionCoordinator  # noqa: E402
from demo_runtime.mission_errors import ExecutionRefused, HardwareMappingUnavailable, MissionCancelled  # noqa: E402

from autonomy.manipulation import hybrid_pick_place as hpp  # noqa: E402

FAKE_ENTRY = "packages/demo-runtime/tests/fake_controller.py"
WAIT_S = 30.0
NEAR = {"target_id": "cube_near", "label": "near", "reachable": True, "requires_capability": False}
FAR = {"target_id": "cube_far", "label": "far", "reachable": False, "requires_capability": True}
AUTO = {"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": "auto"}
# Documentation values only: the fake controller never opens any of them.
ENDPOINTS = {"admin_probe": "/opt/example/cake-admin-probe", "admin_socket": "/run/example/cake-admin.sock",
             "slot_uuid": "0123456789abcdef0123456789abcdef", "capsule": "ab" * 32,
             "observation_endpoint": "ipc:///run/example/approach-observations.ipc",
             "command_endpoint": "ipc:///run/example/approach-commands.ipc"}
# TEST VALUES for the claim and match radii (the design's D3 starting points); a mission needs some, and these are not qualified.
TEST_RADII = {"r_claim_mm": 30.0, "r_match_mm": 40.0, "basis": "test values only (design D3 starting points)"}
AVAILABLE = lambda: (True, None)  # noqa: E731 -- a STAND-IN for an available source
UNAVAILABLE_REASON = "stood_in_unavailable"
UNAVAILABLE = lambda: (False, UNAVAILABLE_REASON)  # noqa: E731 -- a STAND-IN for a source with nothing to hand over
REAL = None  # the runner's own answer: target_observation_source.capability_observation_available


def descriptors_of(path) -> list:
    """This process's open descriptors on `path`, read from /proc/self/fd (Linux, where the runtime runs)."""
    table = Path("/proc/self/fd")
    if not table.is_dir():
        raise unittest.SkipTest("no /proc/self/fd here to count open descriptors")
    wanted, found = os.path.realpath(path), []
    for entry in table.iterdir():
        try:
            if os.path.realpath(os.readlink(entry)) == wanted:
                found.append(entry.name)
        except OSError:  # the descriptor that listed the directory is gone by now
            continue
    return found


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


class Slot:
    """A capability slot a test flips. `submit` can be held until the test lets it go (a resident taking its time)."""

    def __init__(self, available=False, hold=None):
        self.available_flag, self.hold, self.submitted, self.reads = available, hold, [], 0

    def adapter(self):
        return CapabilityAdapter(available=self._available, submit=self._submit,
                                 observe=lambda: self.available_flag, candidates=FakeCandidates())

    def _available(self):  # the gate's read: counted, so a test can say the gate was never reached
        self.reads += 1
        return self.available_flag

    def _submit(self, operation_uuid, payload):
        if self.hold is not None:
            self.hold.wait(WAIT_S)
        self.submitted.append(operation_uuid)
        self.available_flag = True
        return {"outcome": cake_activation.ACCEPTED, "transaction_phase": "COMPLETED", "transaction_uuid": "t1",
                "detail": "the transaction completed"}


class Stack:
    """A synthetic supervisor with the fake controller, a HardwareExecution, a ProvenTargetRunner and the coordinator."""

    def __init__(self, test, *, env=None, slot=None, step_s="0.01", endpoints=ENDPOINTS, radii=TEST_RADII, observation=AVAILABLE):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-mission-exec-"))
        test.addCleanup(self._remove)
        self.data = self.tmp / "data"
        self.data.mkdir(mode=0o700)
        self.launch_log = self.tmp / "launches.txt"
        self.supervisor = sv.Supervisor(
            data_dir=self.data, journal=Journal(self.data / "journal.jsonl"), event_log=EventLog(self.data / "events.jsonl"),
            runtime_session="rt-test-mission", mode=sv.SYNTHETIC, source_root=REPO_ROOT, controller_entry=FAKE_ENTRY,
            extra_env={"KC_FAKE_STEP_S": step_s, "KC_FAKE_LAUNCH_LOG": str(self.launch_log), **(env or {})})
        self.hardware = mx.HardwareExecution(radii=radii)
        self.evidence, self.asked = [], []

        def asking():
            self.asked.append(len(self.launches()))  # how many runs had been launched when the runner asked
            return (observation or mx.target_observation_module().capability_observation_available)()

        self.runner = mx.ProvenTargetRunner(hardware=self.hardware, supervisor=self.supervisor, capability_endpoints=endpoints,
                                            evidence=self.evidence.append, observation_available=asking)
        self.slot = slot or Slot(available=True)
        self.coordinator = MissionCoordinator(str(self.data / mission.MISSIONS_FILE_NAME), self.hardware.adapter(),
                                              self.slot.adapter(), data_mode="live", run_target=self.runner)
        self.runner.bind_recorder(self.coordinator)

    def _remove(self):
        self.coordinator.close()
        if self.supervisor.active_run() is not None and not self.supervisor.detached:
            self.supervisor.stop()
            deadline = time.monotonic() + WAIT_S
            while self.supervisor.active_run() is not None and time.monotonic() < deadline:
                time.sleep(0.02)
        self.supervisor.detach()
        for line in self.launches():  # a child a detached supervisor let go of: ask it to stop, by PID, and wait for it
            pid = int(line.split()[0])
            for _ in range(200):
                if not sv.is_alive(pid):
                    break
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def launches(self):
        return self.launch_log.read_text().splitlines() if self.launch_log.exists() else []

    def target_file(self, run_id):
        return mx.target_binding_module().load_mission_target(str(self.data / "history" / run_id / sv.MISSION_TARGET_FILE))

    def finish(self, targets):
        started = self.coordinator.start(targets)
        self.test.assertTrue(started["ok"], started)
        self.test.assertTrue(self.coordinator.join(WAIT_S), "the mission did not end in time")
        return self.coordinator.state(started["mission_id"])

    def records(self):
        return read_json_lines(self.data / mission.MISSIONS_FILE_NAME)[0]

    def wait_until(self, predicate, what):
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.test.fail(f"{what} did not happen in time")


class TheGraspPositionRecord(unittest.TestCase):
    """The precise GRASP_POSITION request, the guard that refuses it, and no send."""

    def test_the_pinned_request_is_the_hybrid_machines_own(self):
        self.assertEqual(mx.HYBRID_GRASP_POSE_DEG, hpp.KNOWN_GOOD_GRASP_POSE_DEG)
        self.assertEqual(mx.HYBRID_GRASP_DURATION_S, hpp.DEFAULT_MOVE_DURATION_S)
        self.assertEqual(mx.HYBRID_GRASP_REQUEST, ({"shoulder_pan": -10.549, "shoulder_lift": 43.121, "elbow_flex": 54.286,
                                                    "wrist_flex": -33.714}, 2.0, 0.0))

    def test_the_hybrid_machine_reaches_grasp_position_and_the_guard_refuses_it_with_zero_sends(self):
        requests, sends, reads = [], [], []

        def read_pose():  # read-only fakes: a pose and a stationary base, nothing moves
            reads.append("pose")
            return {"shoulder_pan": 0.0, "shoulder_lift": -80.0, "elbow_flex": 35.0, "wrist_flex": 67.0, "wrist_roll": 0.0, "gripper": 0.0}

        def read_base():
            reads.append("base")
            return {"x_vel": 0.0, "y_vel": 0.0, "theta_vel": 0.0}

        def move_arm(target, duration_s, speed):
            requests.append((dict(target), duration_s, speed))
            return mx.refuse_hybrid_arm_request(target, duration_s, speed)

        def move_base(vx, duration_s):
            sends.append((vx, duration_s))
            return True

        def confirm(prompt):
            raise AssertionError("no gate may be answered before the refusal")

        deps = hpp.PickPlaceDeps(read_pose=read_pose, read_base=read_base, move_arm=move_arm, move_base_sustained=move_base,
                                 confirm=confirm, log=lambda *args, **kwargs: None)
        with self.assertRaises(HardwareMappingUnavailable) as raised:
            hpp.run_hybrid_pick_place(deps)
        self.assertEqual(str(raised.exception), mx.GRASP_REFUSAL)
        self.assertEqual(requests, [mx.HYBRID_GRASP_REQUEST], "exactly the one GRASP_POSITION request, and it was refused")
        self.assertEqual(sends, [], "zero base sends")
        self.assertEqual(reads, ["pose", "base"], "PRECHECK read the arm and the base, and nothing else happened")

    def test_any_other_arm_request_is_refused_too(self):
        with self.assertRaises(HardwareMappingUnavailable) as raised:
            mx.refuse_hybrid_arm_request({"shoulder_pan": -10.0}, 2.0, 0.0)
        self.assertEqual(str(raised.exception), mx.ARM_REFUSAL)

    def test_every_step_of_the_hardware_adapter_refuses_and_it_says_it_moves(self):
        adapter = mx.HardwareExecution().adapter()
        self.assertTrue(adapter.performs_motion)
        for call, args in ((adapter.read_pose, ()), (adapter.read_base, ()), (adapter.move_arm, ({}, 2.0, 0.0)),
                           (adapter.move_base_sustained, (0.05, 1.5)), (adapter.confirm, ("CUBE READY?",))):
            with self.assertRaises(HardwareMappingUnavailable):
                call(*args)

    def test_the_hybrid_machine_on_hardware_fails_at_its_first_callable(self):
        tmp = tempfile.mkdtemp(prefix="kc-mission-hybrid-")
        self.addCleanup(shutil.rmtree, tmp, True)
        coordinator = MissionCoordinator(os.path.join(tmp, mission.MISSIONS_FILE_NAME), mx.HardwareExecution().adapter(),
                                         Slot(available=True).adapter(), data_mode="live")
        self.addCleanup(coordinator.close)
        started = coordinator.start([dict(NEAR)])
        self.assertTrue(coordinator.join(WAIT_S))
        view = coordinator.state(started["mission_id"])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertTrue(view["detail"].startswith("HardwareMappingUnavailable: read_pose refused"), view["detail"])


class TheOutcomeMapping(unittest.TestCase):
    def test_two_targets_placed_bound_and_associated(self):
        stack = Stack(self)
        view = stack.finish([dict(NEAR), dict(FAR)])
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        self.assertEqual([target["state"] for target in view["targets"]], [mission.TARGET_PLACED, mission.TARGET_PLACED])
        runs = [target["runs"] for target in view["targets"]]
        self.assertTrue(all(len(run_ids) == 1 for run_ids in runs), runs)
        self.assertEqual(len(stack.launches()), 2)
        self.assertEqual(view["targets"][0]["binding"]["mode"], "ARM_ONLY")
        self.assertEqual(view["targets"][1]["binding"]["mode"], "BASE_ADVANCE")
        self.assertEqual((3, 3), (view["targets"][1]["evidence"]["capability"]["accepted"], view["targets"][1]["evidence"]["capability"]["applied"]))
        # The placement evidence is the release agreement's (the pad policy of 2026-09-29); the retired claim fields are gone.
        evidence = view["targets"][0]["evidence"]
        self.assertEqual((1, 4.0, 26.0), (evidence["on_pad_count"], evidence["release_distance_mm"], evidence["release_tolerance_mm"]))
        self.assertFalse({"unclaimed_placed_count", "earlier_targets_undisturbed"} & set(evidence))
        started = [record for record in stack.records() if record.get("run_started")]
        self.assertEqual([record["target_id"] for record in started], ["cube_near", "cube_far"])
        # The second target's file carries the first execution's bindings and the first target's placed position.
        second_run = runs[1][0]
        binding = mx.target_binding_module()
        target = binding.load_mission_target(str(stack.data / "history" / second_run / sv.MISSION_TARGET_FILE))
        self.assertEqual(target.targets[0].placed_pad_m, (0.0, 0.0))
        self.assertIsNotNone(target.targets[1].bound_pad_m)
        self.assertEqual(target.capability.slot_uuid, ENDPOINTS["slot_uuid"])
        self.assertEqual(target.radii.to_document(), TEST_RADII)
        # The need was the BINDING's measurement, asked once, after the first run and before the second.
        self.assertEqual([("mission_capability_required", "cube_far", mx.SOURCE_BINDING, None, True)],
                         [(e["rec"], e["target_id"], e["source"], e["run_id"], e["observation_available"]) for e in stack.evidence])
        self.assertEqual([1], stack.asked)
        self.assertIsNone(stack.target_file(runs[0][0]).capability, "the arm-only target carried nothing")

    def test_a_result_that_echoes_another_mission_is_refused(self):
        stack = Stack(self, env={"KC_FAKE_MISSION_RESULT": "echo_mismatch"})
        view = stack.finish([dict(NEAR)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertEqual(view["targets"][0]["state"], mission.TARGET_FAILED)
        self.assertEqual(view["detail"], f"{mx.RESULT_REFUSED}:result_mission_mismatch")

    def test_a_run_with_no_result_file_is_not_placed(self):
        stack = Stack(self, env={"KC_FAKE_MISSION_RESULT": "absent"})
        view = stack.finish([dict(NEAR)])
        self.assertEqual(view["detail"], f"{mx.RESULT_REFUSED}:result_file_missing")
        self.assertEqual(view["targets"][0]["evidence"]["outcome"], "placed_new", "the run said placed; the mission did not take its word")

    def test_already_placed_is_a_failure_of_the_target(self):
        stack = Stack(self, env={"KC_FAKE_SCENARIO": "already_placed"})
        view = stack.finish([dict(NEAR)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertTrue(view["detail"].startswith("run_outcome:already_placed_noop"), view["detail"])

    def test_a_capability_target_without_endpoints_is_refused_before_any_run(self):
        stack = Stack(self, endpoints=None)
        view = stack.finish([dict(FAR)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertTrue(view["detail"].startswith("HardwareMappingUnavailable"), view["detail"])
        self.assertEqual(stack.launches(), [])


class TheStop(unittest.TestCase):
    def test_a_blocking_child_is_stopped_in_the_daemons_order_and_the_mission_ends_stopped_with_its_run(self):
        stack = Stack(self, step_s="0.5")
        started = stack.coordinator.start([dict(NEAR), dict(FAR)])
        stack.wait_until(lambda: stack.supervisor.active_run() is not None and stack.launches(), "the first run")
        stack.wait_until(lambda: any(event["event"]["kind"] == "state.transition"
                                     for event in read_json_lines(stack.data / "events.jsonl")[0]), "the fake in its script")
        begun = time.monotonic()
        self.assertEqual(stack.coordinator.cancel_now(), started["mission_id"])
        stack.supervisor.stop(origin="operator_console")
        answer = stack.coordinator.stop(origin="mission_stop")
        elapsed = time.monotonic() - begun
        self.assertTrue(answer["stopping"])
        self.assertLess(elapsed, 0.2, "the stop is answered at once, not after the child")
        self.assertTrue(stack.coordinator.join(WAIT_S))
        view = stack.coordinator.state(started["mission_id"])
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1, "one launch, and the second target never started")
        run_id = stack.launches()[0].split()[1]
        self.assertEqual(view["targets"][0]["runs"], [run_id])
        self.assertEqual(view["targets"][0]["state"], mission.TARGET_STOPPED)
        self.assertEqual(view["targets"][1]["state"], mission.TARGET_PENDING)

    def test_the_wait_alone_delivers_a_stop_the_supervisor_did_not_see(self):
        stack = Stack(self, step_s="0.5")
        started = stack.coordinator.start([dict(NEAR)])
        stack.wait_until(lambda: stack.supervisor.active_run() is not None, "the run")
        stack.coordinator.stop()  # only the coordinator: the wait's cancel.wait must pass the stop on
        self.assertTrue(stack.coordinator.join(WAIT_S))
        self.assertEqual(stack.coordinator.state(started["mission_id"])["state"], mission.MISSION_STOPPED)
        completion = stack.supervisor.table()[next(iter(stack.supervisor.table()))]["completion"]
        self.assertEqual(completion["stop"]["origin"], mx.STOP_ORIGIN)

    def test_a_stop_during_the_capability_wait_is_answered_within_one_poll_and_starts_nothing(self):
        """The binding's measurement: NEAR placed, FAR bound BASE_ADVANCE, its gate waits; the stop starts no FAR run."""
        stack = Stack(self, slot=Slot(available=False))
        started = stack.coordinator.start([dict(NEAR), dict(FAR)])
        stack.wait_until(lambda: (stack.coordinator.state(started["mission_id"]) or {}).get("state") == mission.MISSION_WAITING,
                         "the wait")
        begun = time.monotonic()
        stack.coordinator.cancel_now()
        stack.coordinator.stop()
        self.assertTrue(stack.coordinator.join(CAPABILITY_POLL_S + 1.0))
        self.assertLess(time.monotonic() - begun, CAPABILITY_POLL_S + 0.5)
        self.assertEqual(stack.coordinator.state(started["mission_id"])["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1, "only NEAR's run; nothing was started for FAR")

    def test_a_stop_between_the_measuring_run_and_the_respawn_starts_nothing(self):
        """The SEARCH's measurement: a lone FAR's first run refuses at SEARCH, its gate waits, and the stop ends it there."""
        stack = Stack(self, slot=Slot(available=False))
        started = stack.coordinator.start([dict(FAR)])
        stack.wait_until(lambda: (stack.coordinator.state(started["mission_id"]) or {}).get("state") == mission.MISSION_WAITING,
                         "the wait")
        stack.coordinator.cancel_now()
        stack.coordinator.stop()
        self.assertTrue(stack.coordinator.join(CAPABILITY_POLL_S + 1.0))
        self.assertEqual(stack.coordinator.state(started["mission_id"])["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1, "the measuring run only; no respawn after the stop")

    def test_with_the_real_source_a_stop_after_the_measuring_run_ended_is_a_stop_not_a_failure(self):
        """The REAL source, whatever it would answer. A lone FAR's first run refuses at SEARCH and ENDS ON ITS OWN (exit 5,
        not a stop); the stop lands after that run ended, while its bindings are recorded, and before the source is
        asked. The admission reads the stop BEFORE it asks, so the mission ends STOPPED: nothing is asked or recorded,
        and nothing respawns. Asking first would have written an evidence record for an answer nothing acts on (under v2
        the real source answers available) and only then raised MissionCancelled from the check that follows the write;
        the ordering is what keeps the STOPPED mission clean of any ask or record."""
        stack = Stack(self, observation=REAL)
        coordinator = stack.coordinator

        class StopWhenBound:  # the recorder the runner writes the measuring run's bindings through, after that run ended
            def on_run_started(self, *args, **kwargs):
                coordinator.on_run_started(*args, **kwargs)

            def on_target_bound(self, *args, **kwargs):
                coordinator.on_target_bound(*args, **kwargs)
                coordinator.cancel_now()

        stack.runner.bind_recorder(StopWhenBound())
        started = coordinator.start([dict(FAR)])
        self.assertTrue(coordinator.join(WAIT_S), "the mission did not end in time")
        view = coordinator.state(started["mission_id"])
        self.assertEqual(view["state"], mission.MISSION_STOPPED, view)
        self.assertEqual(len(stack.launches()), 1, "the measuring run only; no respawn after the stop")
        completion = next(iter(stack.supervisor.table().values()))["completion"]
        self.assertEqual((completion["exit_status"], completion.get("stop")), (mx.CYCLE_FAILED_EXIT_STATUS, None),
                         "the measuring run ended on its own: the stop came after it")
        self.assertEqual(([], [], 0), (stack.asked, stack.evidence, stack.slot.reads))

    def test_with_the_real_source_a_stop_before_a_bound_targets_admission_is_a_stop(self):
        """The binding's measurement, with the REAL source: FAR bound BASE_ADVANCE by an earlier run of this mission, and a
        stop already set when its admission starts. MissionCancelled, with nothing asked, recorded, gated or started."""
        hardware = mx.HardwareExecution(radii=TEST_RADII)
        adapter = hardware.adapter()
        adapter.attach_mission("mission-0123456789ab", [dict(NEAR), dict(FAR)])
        cancel = threading.Event()
        adapter.attach_cancel(cancel)
        hardware.record_measured([{"target_id": "cube_far", "mode": "BASE_ADVANCE"}])
        cancel.set()
        asked, recorded, gated = [], [], []

        class NoRun:
            def start_mission_run(self, run):
                raise AssertionError("a run was started after the stop")

        def real_source():
            asked.append(True)
            return mx.target_observation_module().capability_observation_available()

        with self.assertRaises(MissionCancelled):
            mx.run_proven_target(None, lambda name: gated.append(name) or True, lambda: None, dict(FAR), hardware=hardware,
                                 supervisor=NoRun(), recorder=object(), capability_endpoints=ENDPOINTS,
                                 evidence=recorded.append, observation_available=real_source)
        self.assertEqual(([], [], []), (asked, recorded, gated))

    def test_an_accepted_activation_after_the_stop_resumes_nothing_and_spawns_nothing(self):
        hold = threading.Event()
        stack = Stack(self, slot=Slot(available=False, hold=hold))
        started = stack.coordinator.start([dict(NEAR), dict(FAR)])
        mission_id = started["mission_id"]
        stack.wait_until(lambda: (stack.coordinator.state(mission_id) or {}).get("state") == mission.MISSION_WAITING, "the wait")
        answers = []
        submitter = threading.Thread(target=lambda: answers.append(stack.coordinator.submit_activation("mobile_approach.activate")))
        submitter.start()
        stack.wait_until(lambda: any(record.get("rec") == "mission_activation" for record in stack.records()), "the attempt record")
        stack.coordinator.cancel_now()
        stack.coordinator.stop()
        hold.set()  # the resident answers ACCEPTED only now, after the stop
        submitter.join(WAIT_S)
        self.assertTrue(stack.coordinator.join(WAIT_S))
        self.assertEqual(answers[0]["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertTrue(answers[0]["stopped_during_activation"])
        view = stack.coordinator.state(mission_id)
        self.assertEqual(view["state"], mission.MISSION_STOPPED)
        self.assertEqual(len(stack.launches()), 1, "NEAR's run only: no spawn after the stop, whatever the slot now carries")
        self.assertIsNone(stack.supervisor.active_run())
        self.assertFalse(any(record.get("resumed") for record in stack.records()), "the target never resumed")


    def test_the_wait_paces_itself_while_a_stop_goes_unacknowledged(self):
        """Once the cancel event is set, cancel.wait returns at once. The wait must still pause every turn: a controller
        that has not acknowledged the stop runs on (on the robot, up to one frame in flight) and this process must not
        spend a core reading the command table while it does. never_acknowledges runs its script to the end."""
        stack = Stack(self, step_s="0.1", env={"KC_FAKE_SCENARIO": "never_acknowledges"})
        stack.coordinator.start([dict(NEAR)])
        stack.wait_until(lambda: stack.supervisor.active_run() is not None and stack.launches(), "the run")
        stack.wait_until(lambda: any(event["event"]["kind"] == "state.transition"
                                     for event in read_json_lines(stack.data / "events.jsonl")[0]), "the fake in its script")
        cpu_begun, wall_begun = time.process_time(), time.monotonic()
        stack.coordinator.cancel_now()
        stack.supervisor.stop(origin="operator_console")
        stack.coordinator.stop(origin="mission_stop")
        self.assertTrue(stack.coordinator.join(WAIT_S))
        cpu, wall = time.process_time() - cpu_begun, time.monotonic() - wall_begun
        self.assertGreater(wall, 1.0, "the child ran on after the stop, so there was time for a wait to spin")
        self.assertLess(cpu, 0.25 * wall, f"the wait spun: {cpu:.2f} s of CPU in {wall:.2f} s")
        self.assertEqual(len(stack.launches()), 1)

    def test_a_daemon_shutdown_during_a_run_sends_the_controller_nothing_and_is_not_a_stop(self):
        """daemon.RuntimeCore.close: supervisor.detach(), THEN the coordinator's close, which sets the mission's cancel
        event. The wait it wakes must see the detach and let go: no SIGINT, no stop.requested with a person's origin, no
        stop request in the mission's record. The mission ends FAILED with RUNTIME_DETACHED and the child runs on."""
        stack = Stack(self, step_s="0.3")
        started = stack.coordinator.start([dict(NEAR)])
        stack.wait_until(lambda: stack.supervisor.active_run() is not None and stack.launches(), "the run")
        stack.wait_until(lambda: any(event["event"]["kind"] == "state.transition"
                                     for event in read_json_lines(stack.data / "events.jsonl")[0]), "the fake in its script")
        pid = int(stack.launches()[0].split()[0])
        stack.supervisor.detach()
        stack.coordinator.close()
        with stack.supervisor._state_lock:
            run = stack.supervisor._active
            signals, origin = run.stop_signals_sent, run.stop_origin
        self.assertEqual((signals, origin), (0, None), "the shutdown sent the controller a stop")
        self.assertEqual([event for event in read_json_lines(stack.data / "events.jsonl")[0]
                          if event["event"]["kind"] == "stop.requested"], [])
        self.assertTrue(sv.is_alive(pid), "the controller runs on by itself after a shutdown")
        records = [record for record in stack.records() if record.get("mission_id") == started["mission_id"]]
        self.assertFalse(any(record.get("rec") == "mission_stop" for record in records), "no stop request on the record")
        ended = [record for record in records if record.get("rec") == "mission" and record.get("state") != mission.MISSION_RUNNING]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["state"], mission.MISSION_FAILED)
        self.assertIn(mx.RUNTIME_DETACHED, ended[0]["detail"])


class TheWaitAlone(unittest.TestCase):
    """_wait_for_run against a stub supervisor: the pacing and the order of its two reads, with no child at all."""

    class StubSupervisor:
        def __init__(self, finish_after_s, detached=False):
            self.begun, self.finish_after_s, self.detached = time.monotonic(), finish_after_s, detached
            self.reads, self.stops = 0, []

        def command_state(self, command_id):
            self.reads += 1
            return {"status": "finished" if time.monotonic() - self.begun > self.finish_after_s else "running"}

        def active_run(self):
            return {"command_id": "c1"}

        def stop(self, origin):
            self.stops.append(origin)

    def test_a_stopping_wait_reads_once_per_poll_and_stops_once(self):
        cancel = threading.Event()
        cancel.set()
        stub = self.StubSupervisor(finish_after_s=1.0)
        state = mx._wait_for_run(stub, "c1", cancel, 0.2)
        self.assertEqual(state["status"], "finished")
        self.assertLessEqual(stub.reads, 10, f"{stub.reads} reads in about 1 s at a 0.2 s poll")
        self.assertEqual(stub.stops, [mx.STOP_ORIGIN])

    def test_a_set_event_on_a_detached_supervisor_is_a_shutdown_and_stops_nothing(self):
        cancel = threading.Event()
        cancel.set()
        stub = self.StubSupervisor(finish_after_s=60.0, detached=True)
        with self.assertRaises(ExecutionRefused) as raised:
            mx._wait_for_run(stub, "c1", cancel, 0.2)
        self.assertIn(mx.RUNTIME_DETACHED, str(raised.exception))
        self.assertEqual(stub.stops, [])


class TheCapabilityFollowsTheMeasurement(unittest.TestCase):
    """ "The need for the capability must follow the target's measured reachability, not merely its ordinal position in
    the mission." The declaration never opens the gate; the measurement leads to it, and only through an available
    observation source: the stand-ins UNAVAILABLE and AVAILABLE prove each path, and one test asks the REAL source and
    pins the v2 answer's consequence (the gate passed, the respawn carrying the capability)."""

    def test_no_radii_no_run_for_any_target(self):
        stack = Stack(self, radii=None)
        view = stack.finish([dict(NEAR), dict(FAR)])
        self.assertEqual((view["state"], view["detail"]), (mission.MISSION_FAILED, mx.RADII_UNQUALIFIED))
        self.assertEqual((stack.launches(), stack.slot.reads, stack.evidence), ([], 0, []))

    def test_with_the_real_source_a_bound_base_advance_target_passes_the_gate_and_is_run_carrying_the_capability(self):
        """The v2 answer through the runtime, no stand-in: NEAR's run binds FAR as BASE_ADVANCE, the runner asks
        target_observation_source.capability_observation_available (available under kc.target_observation/2), the gate
        is read, and FAR's one run carries the capability's endpoints and claims its placement on three APPLIED commands.
        This is the test that once pinned (False, target_observation_mapping_undefined) under v1."""
        stack = Stack(self, observation=REAL)
        view = stack.finish([dict(NEAR), dict(FAR)])
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        self.assertEqual([t["state"] for t in view["targets"]], [mission.TARGET_PLACED, mission.TARGET_PLACED])
        self.assertEqual(len(stack.launches()), 2, "one run per target; FAR's the respawn-free carrying run")
        self.assertGreaterEqual(stack.slot.reads, 1, "the gate was read")
        self.assertEqual([1], stack.asked, "asked once, after NEAR's run bound FAR and before FAR's run")
        self.assertEqual([(mx.SOURCE_BINDING, True, None)],
                         [(e["source"], e["observation_available"], e["reason"]) for e in stack.evidence])
        self.assertEqual({"accepted": 3, "applied": 3},
                         {k: view["targets"][1]["evidence"]["capability"][k] for k in ("accepted", "applied")})
        far_run = view["targets"][1]["runs"][0]
        self.assertEqual(stack.target_file(far_run).capability.slot_uuid, ENDPOINTS["slot_uuid"], "FAR's run carried the capability")

    def test_with_an_unavailable_source_a_bound_base_advance_target_fails_with_no_gate_and_no_spawn(self):
        stack = Stack(self, observation=UNAVAILABLE)
        view = stack.finish([dict(NEAR), dict(FAR)])
        self.assertEqual([t["state"] for t in view["targets"]], [mission.TARGET_PLACED, mission.TARGET_FAILED])
        self.assertEqual(view["detail"], f"{mx.CAPABILITY_OBSERVATION_UNAVAILABLE}:{UNAVAILABLE_REASON}")
        self.assertEqual(len(stack.launches()), 1, "no run was started for FAR")
        self.assertEqual(stack.slot.reads, 0, "the gate was never reached")
        self.assertEqual([(mx.SOURCE_BINDING, False, UNAVAILABLE_REASON)],
                         [(e["source"], e["observation_available"], e["reason"]) for e in stack.evidence])

    def test_the_runners_default_seam_is_the_real_source_and_answers_in_the_contracts_shape(self):
        """Without a stand-in the runner asks target_observation_source.capability_observation_available. Its VALUE is
        that module's own concern (autonomy/tests/test_target_observation_source.py) and is not pinned here: only that the
        seam is wired and answers (bool, reason or None), so a runner built without a stand-in never asks a fake."""
        runner = mx.ProvenTargetRunner(hardware=mx.HardwareExecution(radii=TEST_RADII), supervisor=object(), capability_endpoints=ENDPOINTS)
        self.assertIsNone(runner.observation_available, "no stand-in: the runner falls back to the real source")
        available, reason = mx.target_observation_module().capability_observation_available()
        self.assertIsInstance(available, bool)
        self.assertTrue(reason is None if available else isinstance(reason, str) and reason, (available, reason))

    def test_the_declaration_alone_never_opens_the_gate_the_search_measurement_does_and_one_respawn_follows(self):
        stack = Stack(self)
        view = stack.finish([dict(FAR)])
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        first, second = view["targets"][0]["runs"]
        self.assertEqual(len(stack.launches()), 2)
        self.assertEqual([1], stack.asked, "availability was asked once, after the measuring run and not before it")
        self.assertIsNone(stack.target_file(first).capability, "the first run did not carry the capability: nothing had measured the cube")
        self.assertEqual(stack.target_file(second).capability.slot_uuid, ENDPOINTS["slot_uuid"])
        self.assertTrue(stack.target_file(second).first_execution, "the respawn binds again, from its own fresh SEARCH")
        self.assertEqual([(mx.SOURCE_SEARCH, first, "BASE_ADVANCE", True)],
                         [(e["source"], e["run_id"], e["measured_mode"], e["observation_available"]) for e in stack.evidence])
        self.assertGreaterEqual(stack.slot.reads, 1)

    def test_with_an_unavailable_source_the_search_measurement_fails_the_target_after_the_one_measuring_run(self):
        stack = Stack(self, observation=UNAVAILABLE)
        view = stack.finish([dict(FAR)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertEqual(view["detail"], f"{mx.CAPABILITY_OBSERVATION_UNAVAILABLE}:{UNAVAILABLE_REASON}")
        self.assertEqual((len(stack.launches()), stack.slot.reads), (1, 0))
        self.assertEqual(view["targets"][0]["runs"], [stack.launches()[0].split()[1]], "the measuring run stays associated with FAR")

    def test_the_respawn_happens_once(self):
        stack = Stack(self, env={"KC_FAKE_MISSION_RESULT": "capability_required_always"})
        view = stack.finish([dict(FAR)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertEqual(view["detail"], f"run_outcome:failed:{mx.CAPABILITY_REQUIRED}")
        self.assertEqual(len(stack.launches()), 2)
        self.assertEqual([mx.SOURCE_SEARCH], [e["source"] for e in stack.evidence])

    def test_a_search_refusal_is_recognized_only_whole(self):
        refusal = {"stage": "finished", "outcome": "failed", "reason_code": mx.CAPABILITY_REQUIRED, "selection": {"mode": "BASE_ADVANCE"},
                   "placement": None, "predicates": {"object_secured": None, "placed": None, "base_returned": None},
                   "final_state": mx.SEARCH_REFUSAL_FINAL_STATE}
        self.assertTrue(mx._search_measured_capability(refusal, mx.CYCLE_FAILED_EXIT_STATUS))
        for change in ({"reason_code": "search_no_cube"}, {"selection": {"mode": "ARM_ONLY"}}, {"final_state": "PREGRASP"}, {"outcome": "stopped"},
                       {"predicates": {"object_secured": True, "placed": None, "base_returned": None}}, {"stage": "bound"}):
            with self.subTest(change=change):
                self.assertFalse(mx._search_measured_capability({**refusal, **change}, mx.CYCLE_FAILED_EXIT_STATUS))
        self.assertFalse(mx._search_measured_capability(refusal, 0))
        self.assertFalse(mx._search_measured_capability(None, mx.CYCLE_FAILED_EXIT_STATUS))

    def test_an_arm_alone_bindings_fallback_refusal_is_recognized_only_whole_and_only_with_the_arm_alone(self):
        """The controller's refusal of an auto target bound to the arm alone whose in-run fallback moves the base
        (mission_fallback_capability_required, under the additive placement words): the same whole shape, the selection
        ARM_ONLY; the code with a BASE_ADVANCE selection, and the proven code with an ARM_ONLY one, are not it."""
        refusal = {"stage": "finished", "outcome": "failed", "reason_code": mx.FALLBACK_CAPABILITY_REQUIRED, "selection": {"mode": "ARM_ONLY"},
                   "placement": None, "predicates": {"object_secured": None, "placed": None, "base_returned": None},
                   "final_state": mx.SEARCH_REFUSAL_FINAL_STATE}
        self.assertTrue(mx._search_measured_capability(refusal, mx.CYCLE_FAILED_EXIT_STATUS))
        for change in ({"selection": {"mode": "BASE_ADVANCE"}}, {"reason_code": mx.CAPABILITY_REQUIRED}, {"final_state": "PREGRASP"},
                       {"predicates": {"object_secured": True, "placed": None, "base_returned": None}}, {"placement": {"placed": False}}):
            with self.subTest(change=change):
                self.assertFalse(mx._search_measured_capability({**refusal, **change}, mx.CYCLE_FAILED_EXIT_STATUS))
        self.assertFalse(mx._search_measured_capability(refusal, 0))

    def test_a_measured_mode_is_a_measurement_of_this_mission(self):
        hardware = mx.HardwareExecution(radii=TEST_RADII)
        hardware.record_measured([{"target_id": "cube_far", "mode": "BASE_ADVANCE"}])
        self.assertEqual("BASE_ADVANCE", hardware.measured_mode("cube_far"))
        hardware.adapter().attach_mission("mission-0123456789ab", [dict(NEAR), dict(FAR)])
        self.assertIsNone(hardware.measured_mode("cube_far"), "a new mission starts with nothing measured")

    def test_the_evidence_file_is_created_by_its_first_record_keeps_no_descriptor_and_is_never_folded(self):
        """Nothing closes the evidence file (the daemon closes the coordinator, which does not know it), so no record may
        leave a descriptor open behind it."""
        tmp = tempfile.mkdtemp(prefix="kc-mission-evidence-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, mission.MISSION_EVIDENCE_FILE_NAME)
        journal = mx.EvidenceJournal(path)
        self.assertFalse(os.path.exists(path))
        record = mission.target_capability_required("mission-0123456789ab", "cube_far", source=mx.SOURCE_BINDING, run_id=None,
                                                    measured_mode="BASE_ADVANCE", observation_available=False, reason="x", t_wall_utc="t")
        journal(record)
        journal(record)
        self.assertEqual([record, record], read_json_lines(path)[0])
        self.assertEqual([], descriptors_of(path), "a record left the evidence file open")
        held = Journal(path)  # not vacuous: an open journal on the same file is seen
        self.addCleanup(held.close)
        self.assertEqual(1, len(descriptors_of(path)))
        self.assertEqual({}, mission.fold_missions([record]), "evidence never enters a mission's view")


class TheAutoTarget(unittest.TestCase):
    """requires_capability "auto": the runtime spawns the first execution without the capability and follows what that
    run's own SEARCH measured."""

    def test_the_auto_word_is_the_bindings(self):
        self.assertEqual("auto", mission.REQUIRES_CAPABILITY_AUTO)
        self.assertEqual(mx.target_binding_module().AUTO, mission.REQUIRES_CAPABILITY_AUTO)

    def test_a_block_within_arm_reach_is_placed_by_one_run_with_nothing_asked(self):
        stack = Stack(self)  # the fake's auto mode defaults to ARM_ONLY
        view = stack.finish([dict(AUTO)])
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        (target,) = view["targets"]
        self.assertEqual((target["state"], target["requires_capability"], target["binding"]["mode"]), (mission.TARGET_PLACED, "auto", "ARM_ONLY"))
        self.assertEqual(len(stack.launches()), 1)
        self.assertIsNone(stack.target_file(target["runs"][0]).capability, "the one run carried nothing")
        self.assertEqual(([], [], 0), (stack.evidence, stack.asked, stack.slot.reads), "no need was recorded, no source asked, no slot read")
        self.assertIsNone(target["evidence"]["capability"])
        self.assertEqual(target["evidence"]["mode"], "ARM_ONLY")

    def test_a_block_beyond_arm_reach_measures_then_the_one_respawn_carries_the_capability(self):
        stack = Stack(self, env={"KC_FAKE_AUTO_MODE": "BASE_ADVANCE"})
        view = stack.finish([dict(AUTO)])
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        (target,) = view["targets"]
        first, second = target["runs"]
        self.assertEqual((target["state"], target["binding"]["mode"]), (mission.TARGET_PLACED, "BASE_ADVANCE"))
        self.assertEqual(len(stack.launches()), 2)
        self.assertEqual([1], stack.asked, "availability was asked once, after the measuring run and not before it")
        self.assertIsNone(stack.target_file(first).capability, "the measuring run did not carry the capability")
        self.assertEqual(stack.target_file(second).capability.slot_uuid, ENDPOINTS["slot_uuid"])
        self.assertTrue(stack.target_file(second).first_execution, "the respawn binds again, from its own fresh SEARCH")
        self.assertEqual([("mission_capability_required", "cube", mx.SOURCE_SEARCH, first, "BASE_ADVANCE", True)],
                         [(e["rec"], e["target_id"], e["source"], e["run_id"], e["measured_mode"], e["observation_available"]) for e in stack.evidence])
        self.assertGreaterEqual(stack.slot.reads, 1)
        self.assertEqual((3, 3), (target["evidence"]["capability"]["accepted"], target["evidence"]["capability"]["applied"]))
        started = [record["target_id"] for record in stack.records() if record.get("run_started")]
        self.assertEqual(["cube", "cube"], started, "both runs stay associated with the one target")

    def test_an_arm_alone_binding_whose_fallback_moves_the_base_asks_then_the_one_respawn_carries_the_capability(self):
        """Lead decision 3 on this path: the first execution, spawned without the capability, binds the arm alone and asks
        for the capability before anything is picked up (its fallback moves the base); the need is recorded as measured at
        its SEARCH for that fallback, the gate is passed, and the one respawn carries the capability and places the block."""
        stack = Stack(self, env={"KC_FAKE_ARM_ALONE_FALLBACK": "1"})
        view = stack.finish([dict(AUTO)])
        self.assertEqual(view["state"], mission.MISSION_COMPLETE, view)
        (target,) = view["targets"]
        first, second = target["runs"]
        self.assertEqual((target["state"], target["binding"]["mode"]), (mission.TARGET_PLACED, "ARM_ONLY"))
        self.assertEqual(len(stack.launches()), 2)
        self.assertEqual([1], stack.asked, "availability was asked once, after the measuring run and not before it")
        self.assertIsNone(stack.target_file(first).capability, "the measuring run did not carry the capability")
        self.assertEqual(stack.target_file(second).capability.slot_uuid, ENDPOINTS["slot_uuid"])
        self.assertTrue(stack.target_file(second).first_execution, "the respawn binds again, from its own fresh SEARCH")
        self.assertEqual([("mission_capability_required", "cube", mx.SOURCE_SEARCH_FALLBACK, first, "ARM_ONLY", True)],
                         [(e["rec"], e["target_id"], e["source"], e["run_id"], e["measured_mode"], e["observation_available"]) for e in stack.evidence])
        self.assertGreaterEqual(stack.slot.reads, 1)

    def test_an_arm_alone_binding_whose_fallback_asks_with_an_unavailable_source_fails_with_no_gate_and_no_respawn(self):
        stack = Stack(self, env={"KC_FAKE_ARM_ALONE_FALLBACK": "1"}, observation=UNAVAILABLE)
        view = stack.finish([dict(AUTO)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertEqual(view["detail"], f"{mx.CAPABILITY_OBSERVATION_UNAVAILABLE}:{UNAVAILABLE_REASON}")
        self.assertEqual((len(stack.launches()), stack.slot.reads), (1, 0))

    def test_the_wait_is_recorded_and_the_same_target_resumes_once_the_slot_carries_the_capability(self):
        stack = Stack(self, env={"KC_FAKE_AUTO_MODE": "BASE_ADVANCE"}, slot=Slot(available=False))
        started = stack.coordinator.start([dict(AUTO)])
        stack.wait_until(lambda: (stack.coordinator.state(started["mission_id"]) or {}).get("state") == mission.MISSION_WAITING, "the wait")
        waiting = stack.coordinator.state(started["mission_id"])
        self.assertEqual((waiting["selected_target"], waiting["waiting_reason"]), ("cube", mission.WAITING_ON_CAPABILITY))
        self.assertEqual(waiting["targets"][0]["state"], mission.TARGET_WAITING)
        self.assertEqual(len(stack.launches()), 1, "the measuring run only, while the target waits")
        stack.slot.available_flag = True  # the operator's activation, observed on the slot
        self.assertTrue(stack.coordinator.join(WAIT_S))
        view = stack.coordinator.state(started["mission_id"])
        self.assertEqual((view["state"], view["targets"][0]["state"]), (mission.MISSION_COMPLETE, mission.TARGET_PLACED))
        self.assertEqual(len(stack.launches()), 2)
        self.assertTrue([record for record in stack.records() if record.get("resumed")], "the resume was not recorded")

    def test_with_an_unavailable_source_the_measuring_run_fails_the_target_with_no_gate_and_no_respawn(self):
        stack = Stack(self, env={"KC_FAKE_AUTO_MODE": "BASE_ADVANCE"}, observation=UNAVAILABLE)
        view = stack.finish([dict(AUTO)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertEqual(view["detail"], f"{mx.CAPABILITY_OBSERVATION_UNAVAILABLE}:{UNAVAILABLE_REASON}")
        self.assertEqual((len(stack.launches()), stack.slot.reads), (1, 0))

    def test_an_auto_target_without_configured_endpoints_is_refused_before_any_run(self):
        # Conservative on purpose: a target that MAY need the capability is refused before motion when this runtime could
        # never hand it over, rather than measured first and failed with the block half way.
        stack = Stack(self, endpoints=None)
        view = stack.finish([dict(AUTO)])
        self.assertEqual(view["state"], mission.MISSION_FAILED)
        self.assertTrue(view["detail"].startswith("HardwareMappingUnavailable"), view["detail"])
        self.assertEqual(stack.launches(), [])


class TheBoundaries(unittest.TestCase):
    def test_this_module_never_imports_the_rehearsal(self):
        source = Path(mx.__file__).read_text(encoding="utf-8")
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(("." * node.level) + (node.module or ""))
                names.update(alias.name for alias in node.names)
        self.assertFalse(any("mission_rehearsal" in name for name in names), names)
        probe = ("import sys; sys.path.insert(0, %r); import demo_runtime.mission_execution as m; m.target_observation_module(); "
                 "print('demo_runtime.mission_rehearsal' in sys.modules, any('rehearsal_observation' in n for n in sys.modules))" % str(PACKAGE_DIR))
        loaded = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
        self.assertEqual(loaded.stdout.strip(), "False False", loaded.stderr)

    def test_the_search_refusal_words_are_the_controllers(self):
        tree = ast.parse((REPO_ROOT / "bin" / "laptop" / "manipulation" / "run_single_cube_cycle.py").read_text(encoding="utf-8"))
        codes = {node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "failed"
                 and node.args and isinstance(node.args[0], ast.Constant)}
        self.assertIn(mx.CAPABILITY_REQUIRED, codes)
        self.assertIn(mx.FALLBACK_CAPABILITY_REQUIRED, codes)
        self.assertIn(mx.CAPABILITY_OBSERVATION_UNAVAILABLE, codes)
        self.assertEqual(mx.MODE_BASE_ADVANCE, mx.target_binding_module().MODE_BASE_ADVANCE)
        self.assertEqual(mx.MODE_ARM_ONLY, mx.target_binding_module().MODE_ARM_ONLY)

    def test_the_runner_says_it_starts_a_controller(self):
        self.assertTrue(mx.ProvenTargetRunner.hardware_run_target)
        self.assertTrue(mx.run_proven_target.hardware_run_target)


if __name__ == "__main__":
    unittest.main()
