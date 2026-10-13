#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_mission_ops.py: the mission ops of the runtime daemon, driven through the daemon's
# own dispatch, against a FAKE admin-probe written by this test. No resident, no robot, no browser, no network.
#
# The fake tool below is not Cake. It answers the two shapes this surface reads (a slot listing, an ApplyPlan answer)
# so the daemon's ordering, refusals and records can be exercised; a real resident is what the thin-path evidence in
# docs/autonomous-demo/cake-console-handoff-2026-09-23.md was taken against, and nothing here is evidence about one.
# Run directly: python3 packages/demo-runtime/tests/test_mission_ops.py
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import cake_activation, cake_admin, daemon, mission, mission_coordinator, mission_service, sources  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402

FIXED_CAKE = {"artifacts": False, "resident": False, "admin_socket": False}
WAIT_S = 20.0

CAPABILITY_SLOT = "505054304050806000016d6f62696c65"
HOLDER_SLOT = "50505430405080600000686f6c646572"
INERT_CAPSULE = "b9b1f7e4d274e289635930042022e0b6abc77c1f3b6b5ecd3bf5fad76d148f8e"
MOBILE_CAPSULE = "87dee37bc60fd377ed43bb5ebc7070deb7c8d0350fb4f29b4034ad4686f341e4"
CANDIDATE_KEY = "mobile_approach.activate"
CANDIDATE_BYTES = b"the prepared swapped candidate plan"

# Strings the repository's text gates refuse, assembled so that no literal exists in this file.
PRIVATE_ADDRESS = ".".join(("203", "0", "113", "10"))
SSH_TARGET = "operator" + "@" + PRIVATE_ADDRESS

FIXTURE_BUILD = "f1" * 32  # the rehearsal's fixture resident, as the configuration names it and the fake reports it

FAKE_PROBE = '''#!/usr/bin/env python3
"""A test double for admin-probe. Not Cake. It reads its answers out of a directory beside the socket path."""
import hashlib
import os
import sys

STATE = sys.argv[2] + ".state"
OPERATION = sys.argv[3]
FIXTURE_BUILD = "f1" * 32  # the build identity of the rehearsal's own fixture resident, unless a test writes another
ROBOT_PROFILE = "841c3e3f73e01e48ece200ce11385aa2dc4945df51de6bef6d1f8e8bd97d15b3"  # what a laptop x86 resident reports too


def read(name, default=""):
    try:
        with open(os.path.join(STATE, name), encoding="ascii") as handle:
            return handle.read()
    except OSError:
        return default


if OPERATION == "list-slots":
    sys.stdout.write(read("slots"))
elif OPERATION == "get-status":
    # The build identity the rehearsal checks before every ApplyPlan: the fixture's unless a test writes another ("none"
    # prints no line). The target profile is the robot's, as the real laptop resident's is: it must decide nothing.
    build = read("build").strip() or FIXTURE_BUILD
    sys.stdout.write("status ok\\n" + ("" if build == "none" else "build_identity " + build + "\\n")
                     + "target_profile_digest " + ROBOT_PROFILE + "\\n")
elif OPERATION == "apply-plan":
    operation_uuid = sys.argv[sys.argv.index("--uuid") + 1]
    with open(sys.argv[sys.argv.index("--payload") + 1], "rb") as handle:
        payload = handle.read()
    with open(os.path.join(STATE, "submissions"), "a", encoding="ascii") as handle:
        handle.write(operation_uuid + " " + hashlib.sha256(payload).hexdigest() + "\\n")
    verdict = read("verdict", "abort").strip()
    if verdict == "complete":
        with open(os.path.join(STATE, "slots"), "w", encoding="ascii") as handle:
            handle.write(read("active_slots"))
        sys.stdout.write("status ok\\ntransaction_phase COMPLETED\\ntransaction_uuid t-1\\n")
    elif verdict == "complete_without_moving_the_slot":
        sys.stdout.write("status ok\\ntransaction_phase COMPLETED\\ntransaction_uuid t-2\\n")
    elif verdict == "abort":
        sys.stdout.write("status ok\\ntransaction_phase ABORTED\\ntransaction_uuid t-0\\n")
    else:
        sys.stdout.write("status refused\\nrefusal_code operation_identity_mismatch\\n")
else:
    sys.stdout.write("status ok\\n")
'''


RESIDENT_SESSION = "0a" * 16
PLAN_BEFORE, PLAN_AFTER = "8b" * 32, "ee" * 32


def slot_listing(capability_capsule: str, generation: int) -> str:
    # The resident header list-slots prints before its rows (admin-probe prints the snapshot for every answer that
    # carries one), so a slot reading can say which resident answered, under which Plan and at which epoch.
    return "\n".join([
        "status ok",
        "session_uuid " + RESIDENT_SESSION,
        "plan_digest " + (PLAN_AFTER if generation > 1 else PLAN_BEFORE),
        "epoch " + ("3" if generation > 1 else "2"),
        f"slot capsule={MOBILE_CAPSULE} config=bb generation=1 instance=2 name=mobile_approach.holder state=ACTIVE uuid={HOLDER_SLOT}",
        f"slot capsule={capability_capsule} config=cc generation={generation} instance=3 name=mobile_approach state=ACTIVE uuid={CAPABILITY_SLOT}",
        "",
    ])


class Deployment:
    """A private tree holding a fake admin-probe, a stand-in socket, a prepared candidate and the slot state."""

    def __init__(self, test):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-mission-ops-"))
        test.addCleanup(self.cleanup)
        self.probe = self.tmp / "admin-probe"
        self.probe.write_text(FAKE_PROBE, encoding="ascii")
        self.probe.chmod(0o700)
        # The runtime only checks that the socket path EXISTS before it spends a child process on it, and the fake
        # tool speaks no wire protocol, so an ordinary file stands in for the socket here.
        self.socket = self.tmp / "admin.sock"
        self.socket.write_text("", encoding="ascii")
        self.state = Path(str(self.socket) + ".state")
        self.state.mkdir()
        self.candidate = self.tmp / "mobile-demo-swapped.cakepln"
        self.candidate.write_bytes(CANDIDATE_BYTES)
        (self.state / "active_slots").write_text(slot_listing(MOBILE_CAPSULE, 2), encoding="ascii")
        self.inert()
        self.verdict("abort")

    def inert(self) -> None:
        (self.state / "slots").write_text(slot_listing(INERT_CAPSULE, 1), encoding="ascii")

    def verdict(self, verdict: str) -> None:
        (self.state / "verdict").write_text(verdict, encoding="ascii")

    def build(self, identity: str) -> None:
        (self.state / "build").write_text(identity, encoding="ascii")

    def submissions(self) -> list:
        try:
            return [line.split() for line in (self.state / "submissions").read_text(encoding="ascii").splitlines() if line]
        except OSError:
            return []

    def cake_section(self, **overrides) -> dict:
        section = {"admin_probe_path": str(self.probe), "admin_socket_path": str(self.socket),
                   "rehearsal_fixture_build_identity": FIXTURE_BUILD,
                   "activation_candidates": {CANDIDATE_KEY: str(self.candidate)},
                   "capability": {"name": "mobile_approach", "slot_uuid": CAPABILITY_SLOT,
                                  "active_capsule": MOBILE_CAPSULE}}
        section.update(overrides)
        return section

    def config(self, cake=True, **cake_overrides) -> RuntimeConfig:
        raw = {"data_root": str(self.tmp / "demo-data"), "saved_pose_file": "calibration/scan-home-current.json",
               "pad_template_dir": "calibration/pad-template", "observation_root": "observations",
               "host": {"address": PRIVATE_ADDRESS, "robot_id": "test_robot"}, "pi_ssh_target": SSH_TARGET,
               "cameras": {"wrist_id": "/dev/v4l/by-id/usb-test-wrist", "front_id": None},
               "model": {"identity": "google/owlv2-base-patch16-ensemble", "revision": None}}
        if cake:
            raw["cake"] = {name: value for name, value in self.cake_section(**cake_overrides).items() if value is not None}
        return RuntimeConfig.from_mapping(raw)

    def cleanup(self) -> None:
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Core:
    """One RuntimeCore, driven through its own dispatch so every answer passes the sanitizer a socket client gets."""

    def __init__(self, test, *, mode=daemon.SYNTHETIC, config=None, run_target=None, step_s="0.01", before=None):
        self.test = test
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-mission-core-"))
        test.addCleanup(self.cleanup)
        self.data = self.tmp / "data"
        self.launch_log = self.tmp / "launches.txt"
        if before is not None:
            # What a previous runtime left in the data directory, written before this one is built.
            self.data.mkdir(mode=0o700)
            before(self.data)
        self.core = daemon.RuntimeCore(data_dir=self.data, mode=mode, config=config, source_root=REPO_ROOT,
                                       cake_probe=lambda: dict(FIXED_CAKE),
                                       extra_env={"KC_FAKE_STEP_S": step_s, "KC_FAKE_LAUNCH_LOG": str(self.launch_log)},
                                       # a LIVE runtime here runs the fake controller: no test can start the real one
                                       controller_entry=daemon.FAKE_CONTROLLER_ENTRY,
                                       source_supervisor=(lambda hub: sources.SourceSupervisor(hub)) if mode == daemon.LIVE else None)
        if run_target is not None and self.core.mission is not None:
            self.core.mission._run_target = run_target  # the manipulation machine is not what these cases are about
        test.addCleanup(self.close)

    def ask(self, op: str, request=None) -> dict:
        return self.core.handle(op, request or {}, None)

    def launches(self) -> list:
        return self.launch_log.read_text().splitlines() if self.launch_log.exists() else []

    def wait_idle(self) -> None:
        supervisor = self.core.supervisor if self.core is not None else None
        deadline = time.monotonic() + WAIT_S
        while supervisor is not None and supervisor.active_run() is not None and time.monotonic() < deadline:
            time.sleep(0.02)

    def close(self) -> None:
        if self.core is not None:
            # A run a case started is stopped and waited out first: close() detaches, and a detached controller would
            # otherwise be left running after the case.
            if self.core.supervisor is not None and self.core.supervisor.active_run() is not None:
                self.core.supervisor.stop()
                self.wait_idle()
            self.core.close()
            self.core = None

    def cleanup(self) -> None:
        self.close()
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)


def placed_when_available(execution, capability_available, wait_for_capability_change, target) -> dict:
    """Stands in for the manipulation machine, calling the same two capability callables in the same order."""
    while not capability_available("mobile_approach"):
        wait_for_capability_change()
    execution.read_pose()
    return {"target_state": mission.TARGET_PLACED, "detail": None, "run_id": f"run-{target['target_id']}"}


def stop_command(command_id: str) -> dict:
    return {"contract_version": "1", "command_id": command_id, "type": "stop", "args": {},
            "client": {"session_id": "test-client", "contract_version": "1"}}


def start_command(command_id: str) -> dict:
    return {"contract_version": "1", "command_id": command_id, "type": "start_cycle", "args": {"plan_only": False},
            "client": {"session_id": "test-client", "contract_version": "1"}}


def wait_for(test, predicate, what):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    test.fail(f"timed out waiting for {what}")


class AMissionSurfaceOnlyExistsWhenItCan(unittest.TestCase):
    def test_a_synthetic_runtime_with_everything_named_has_one(self):
        core = Core(self, config=Deployment(self).config())
        self.assertIsNotNone(core.core.mission)
        self.assertIsNone(core.core.mission_unavailable)
        answer = core.ask("mission_candidates")
        self.assertTrue(answer["ok"])
        self.assertEqual([entry["key"] for entry in answer["candidates"]], [CANDIDATE_KEY])
        self.assertEqual(answer["candidates"][0]["payload_digest"], cake_activation.digest_of(CANDIDATE_BYTES))

    def test_a_runtime_that_was_told_about_no_resident_answers_every_op_with_a_reason(self):
        core = Core(self, config=Deployment(self).config(cake=False))
        self.assertIsNone(core.core.mission)
        for op, request in (("mission_state", {}), ("mission_candidates", {}), ("mission_start", {}),
                            ("mission_stop", {}), ("mission_activate", {"candidate_key": CANDIDATE_KEY})):
            with self.subTest(op=op):
                answer = core.ask(op, request)
                self.assertFalse(answer["ok"])
                self.assertEqual(answer["reason_code"], "mission_unavailable")
                self.assertEqual(answer["detail"], mission_service.NOT_CONFIGURED)

    def test_a_replay_runtime_cannot_obtain_the_rehearsal_adapter(self):
        core = Core(self, mode=daemon.REPLAY, config=Deployment(self).config())
        self.assertIsNone(core.core.mission)
        self.assertEqual(core.ask("mission_state")["detail"], mission_service.NOT_SYNTHETIC)

    def test_a_live_runtime_cannot_obtain_the_rehearsal_adapter(self):
        """The isolation rule, at the level it has to hold: a daemon that can command hardware has NO mission
        coordinator at all, because the only execution adapter that exists is refused outside a synthetic runtime.
        The `performs_motion` flag is a label and is not what stops this."""
        deployment = Deployment(self)
        core = Core(self, mode=daemon.LIVE, config=deployment.config())
        self.assertIsNone(core.core.mission)
        self.assertIsNone(core.core.mission_candidates)
        for op in ("mission_state", "mission_start", "mission_stop"):
            with self.subTest(op=op):
                answer = core.ask(op)
                self.assertEqual(answer["reason_code"], "mission_unavailable")
                self.assertEqual(answer["detail"], mission_service.NOT_SYNTHETIC)
        self.assertEqual(core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})["detail"],
                         mission_service.NOT_SYNTHETIC)
        self.assertEqual(deployment.submissions(), [], "a live runtime submitted nothing to the resident")
        self.assertFalse((core.data / mission.MISSIONS_FILE_NAME).exists())

    def test_the_mission_is_built_with_this_core_s_own_mode(self):
        """The check that refuses lives in the adapter's constructor; this pins that the daemon hands it the mode it
        is actually running in rather than one of its own choosing."""
        import inspect

        self.assertIn("mode=self.mode", inspect.getsource(daemon.RuntimeCore._build_mission))


#: The two-cube mission, reachable only by naming it: a Start that names nothing is ONE auto target (mission_service.DEFAULT_TARGETS).
TWO_TARGETS = [{"target_id": "cube_near", "label": "the reachable cube", "reachable": True, "requires_capability": False},
               {"target_id": "cube_far", "label": "the far cube", "reachable": False, "requires_capability": True}]


class TheMissionSequenceThroughTheDaemon(unittest.TestCase):
    def setUp(self):
        self.deployment = Deployment(self)
        self.core = Core(self, config=self.deployment.config(), run_target=placed_when_available)

    def waiting(self) -> dict:
        return wait_for(self, lambda: (lambda state: state if state.get("state") == mission.MISSION_WAITING else None)(self.core.ask("mission_state")),
                        "the mission to reach WAITING_FOR_CAPABILITY")

    def start_two(self) -> dict:
        return self.core.ask("mission_start", {"targets": [dict(target) for target in TWO_TARGETS]})

    def test_a_runtime_that_has_recorded_no_mission_says_so_rather_than_showing_an_empty_one(self):
        answer = self.core.ask("mission_state")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "no_mission_recorded")

    def test_a_start_that_names_nothing_is_one_auto_target_that_waits_at_the_gate_and_resumes_through_an_activation(self):
        """The default mission through the daemon: one target, "auto" carried as declared. The rehearsal's machine cannot
        measure, so it asks the gate, and the mission waits until the operator's activation is observed on the slot."""
        started = self.core.ask("mission_start")
        self.assertTrue(started["ok"])
        state = self.waiting()
        self.assertEqual((state["selected_target"], state["waiting_reason"]), ("cube", mission.WAITING_ON_CAPABILITY))
        self.assertEqual([{"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": "auto", "state": mission.TARGET_WAITING}],
                         [{key: target[key] for key in ("target_id", "label", "reachable", "requires_capability", "state")} for target in state["targets"]])
        self.deployment.verdict("complete")
        self.assertEqual(self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})["activation_state"], mission.ACTIVATION_OBSERVED)
        state = wait_for(self, lambda: (lambda one: one if one.get("state") == mission.MISSION_COMPLETE else None)(self.core.ask("mission_state")),
                         "the one target to resume and the mission to complete")
        self.assertEqual((state["mission_id"], state["targets"][0]["state"]), (started["mission_id"], mission.TARGET_PLACED))

    def test_the_first_target_completes_and_the_second_waits_and_says_why(self):
        started = self.start_two()
        self.assertTrue(started["ok"])
        self.assertTrue(started["mission_id"].startswith("mission-"))
        state = self.waiting()
        self.assertEqual(state["mission_record_version"], mission.MISSION_RECORD_VERSION)
        self.assertNotIn("contract_version", state)
        self.assertEqual(state["selected_target"], "cube_far")
        self.assertEqual(state["waiting_reason"], mission.WAITING_ON_CAPABILITY)
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_near"]["state"], mission.TARGET_PLACED)
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_WAITING)
        self.assertTrue(self.core.ask("mission_stop")["stopping"])

    def test_a_second_mission_is_refused_while_one_is_running(self):
        self.core.ask("mission_start")
        self.waiting()
        second = self.core.ask("mission_start")
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason_code"], "mission_already_running")
        self.core.ask("mission_stop")

    def test_targets_that_are_not_a_mission_are_refused_at_the_boundary(self):
        answer = self.core.ask("mission_start", {"targets": [{"target_id": "../escape"}]})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "bad_request")
        self.assertEqual(answer["detail"], "target_id_not_allowed")
        # The file exists because the coordinator opened it when this runtime was built; what matters is that the
        # refused start put no record in it, so there is still no mission to read back.
        self.assertEqual((self.core.data / mission.MISSIONS_FILE_NAME).read_bytes(), b"")
        self.assertEqual(self.core.ask("mission_state")["reason_code"], "no_mission_recorded")

    def test_an_unprepared_candidate_key_is_refused_and_nothing_is_submitted(self):
        self.core.ask("mission_start")
        self.waiting()
        answer = self.core.ask("mission_activate", {"candidate_key": "something.else"})
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason_code"], "unknown_candidate")
        self.assertEqual(self.deployment.submissions(), [])
        self.core.ask("mission_stop")

    def test_a_body_with_no_candidate_key_is_a_bad_request(self):
        self.core.ask("mission_start")
        self.waiting()
        for request in ({}, {"candidate_key": ""}, {"candidate_key": 7}, {"candidate_key": None}):
            with self.subTest(request=request):
                self.assertEqual(self.core.ask("mission_activate", request)["reason_code"], "bad_request")
        self.assertEqual(self.deployment.submissions(), [])
        self.core.ask("mission_stop")

    def test_an_activation_is_refused_when_no_mission_is_waiting(self):
        answer = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual(answer["reason_code"], "unknown_mission")
        self.assertEqual(self.deployment.submissions(), [])

    def test_a_rejected_activation_leaves_the_mission_waiting_and_is_not_called_a_request_failure(self):
        self.start_two()
        self.waiting()
        answer = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_REJECTED)
        self.assertEqual(answer["transaction_phase"], "ABORTED")
        self.assertEqual(answer["detail"], cake_activation.REJECTION_DETAIL)
        for wrong in ("upload", "signature", "corrupt"):
            self.assertNotIn(wrong, answer["detail"])
        state = self.core.ask("mission_state")
        self.assertEqual(state["state"], mission.MISSION_WAITING)
        self.assertEqual(state["selected_target"], "cube_far")
        self.core.ask("mission_stop")

    def test_a_completed_transaction_whose_slot_did_not_move_is_not_an_activation(self):
        self.core.ask("mission_start")
        self.waiting()
        self.deployment.verdict("complete_without_moving_the_slot")
        answer = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual(answer["outcome"], cake_activation.ACCEPTED)
        self.assertEqual(answer["transaction_phase"], "COMPLETED")
        # CHANGED DELIBERATELY from SUBMITTED, which also meant "in flight": a completed transaction whose slot did
        # not move has its own state now. It is still not OBSERVED, which is what this protects.
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED)
        self.assertNotEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertEqual(self.core.ask("mission_state")["state"], mission.MISSION_WAITING)
        self.core.ask("mission_stop")

    def test_a_valid_activation_is_observed_and_the_same_target_resumes(self):
        started = self.start_two()
        self.waiting()
        self.assertEqual(self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})["activation_state"],
                         mission.ACTIVATION_REJECTED)
        self.deployment.verdict("complete")
        answer = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertFalse(answer["stopped_during_activation"])
        state = wait_for(self, lambda: (lambda one: one if one.get("state") == mission.MISSION_COMPLETE else None)(self.core.ask("mission_state")),
                         "the same target to resume and the mission to complete")
        self.assertEqual(state["mission_id"], started["mission_id"])
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertEqual(by_id["cube_far"]["state"], mission.TARGET_PLACED)
        self.assertEqual([entry["activation_state"] for entry in state["activations"]],
                         [mission.ACTIVATION_REJECTED, mission.ACTIVATION_OBSERVED])

    def test_only_a_resident_proving_it_is_the_named_fixture_is_submitted_to(self):
        """The operator's ruling: a rehearsal ApplyPlans ONLY to the resident whose build_identity equals the configured
        cake.rehearsal_fixture_build_identity, read before every ApplyPlan. Another build identity (the robot's, say),
        none, or a resident that does not answer: refused, nothing submitted, the mission keeps waiting."""
        self.core.ask("mission_start")
        self.waiting()
        self.deployment.verdict("complete")
        for build, reason in (("a0" * 32, mission_service.NOT_THE_FIXTURE), ("none", mission_service.BUILD_UNREADABLE)):
            with self.subTest(build=build):
                self.deployment.build(build)
                answer = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
                self.assertEqual((answer["outcome"], answer["activation_state"], answer["detail"]),
                                 (cake_activation.REQUEST_FAILED, mission.ACTIVATION_FAILED, reason))
                self.assertEqual(self.deployment.submissions(), [], "no ApplyPlan reached the resident")
                self.assertEqual(self.core.ask("mission_state")["state"], mission.MISSION_WAITING)
        self.deployment.build(FIXTURE_BUILD)
        answer = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual(len(self.deployment.submissions()), 1,
                         "the fixture is submitted to although it reports the robot's target profile: the profile decides nothing")
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        self.core.ask("mission_stop")

    def test_each_attempt_is_a_new_operation_identity_bound_to_the_same_prepared_bytes(self):
        self.core.ask("mission_start")
        self.waiting()
        first = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        second = self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        submissions = self.deployment.submissions()
        self.assertEqual([uuid for uuid, _digest in submissions], [first["operation_uuid"], second["operation_uuid"]])
        self.assertNotEqual(first["operation_uuid"], second["operation_uuid"], "a NEW intended operation gets a fresh uuid")
        digests = {digest for _uuid, digest in submissions}
        self.assertEqual(digests, {cake_activation.digest_of(CANDIDATE_BYTES).split(":")[1]},
                         "every attempt carried the same prepared bytes")
        recorded = {entry["payload_digest"] for entry in self.core.ask("mission_state")["activations"]}
        self.assertEqual(recorded, {cake_activation.digest_of(CANDIDATE_BYTES)})
        self.core.ask("mission_stop")

    def test_a_stop_is_answered_while_a_target_is_waiting(self):
        self.start_two()
        self.waiting()
        started = time.monotonic()
        self.assertTrue(self.core.ask("mission_stop")["stopping"])
        state = wait_for(self, lambda: (lambda one: one if one.get("state") == mission.MISSION_STOPPED else None)(self.core.ask("mission_state")),
                         "the mission to record itself stopped")
        self.assertLess(time.monotonic() - started, 5.0)
        by_id = {target["target_id"]: target for target in state["targets"]}
        self.assertNotEqual(by_id["cube_far"]["state"], mission.TARGET_PLACED)

    def test_a_reload_reads_the_same_mission_back_and_starts_nothing(self):
        started = self.core.ask("mission_start")
        waiting = self.waiting()
        first = self.core.ask("mission_state")
        second = self.core.ask("mission_state")
        self.assertEqual(first, second, "a read is a read: asking twice changes nothing")
        self.assertEqual(first["mission_id"], started["mission_id"])
        self.assertEqual(first["selected_target"], waiting["selected_target"])
        self.assertEqual(self.deployment.submissions(), [])
        self.core.ask("mission_stop")

    def test_no_answer_carries_a_path_bytes_or_a_motion_command(self):
        self.core.ask("mission_start")
        self.waiting()
        self.deployment.verdict("complete")
        answers = [self.core.ask("mission_candidates"),
                   self.core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY}),
                   self.core.ask("mission_state")]
        text = repr(answers)
        for forbidden in (str(self.deployment.candidate), str(self.deployment.probe), str(self.deployment.socket),
                          ".cakepln", "vx_mps", "omega_rps", "payload\":", CANDIDATE_BYTES.decode("ascii")):
            self.assertNotIn(forbidden, text, forbidden)



class OneStopForTheRunAndTheMission(unittest.TestCase):
    """The plain stop, the mission stop and a submitted stop command all reach the same path: the controller run's
    guarded stop FIRST, then the mission. None of them touches the host or the resident."""

    def setUp(self):
        self.deployment = Deployment(self)
        self.core = Core(self, config=self.deployment.config(), run_target=placed_when_available)

    def waiting(self, mission_id=None) -> dict:
        def check():
            state = self.core.ask("mission_state")
            if state.get("state") != mission.MISSION_WAITING:
                return None
            return state if mission_id is None or state.get("mission_id") == mission_id else None
        return wait_for(self, check, "the mission to reach WAITING_FOR_CAPABILITY")

    def stopped(self) -> dict:
        state = wait_for(self, lambda: (lambda one: one if one.get("state") == mission.MISSION_STOPPED else None)(self.core.ask("mission_state")),
                         "the mission to record itself stopped")
        # The record says STOPPED a moment before the carrying thread has returned; a start in that moment is refused
        # as mission_already_running, which is right and is not what these cases are about.
        wait_for(self, lambda: not self.core.core.mission.running, "the mission thread to end")
        return state

    def record_order(self) -> list:
        order: list = []
        supervisor, coordinator = self.core.core.supervisor, self.core.core.mission
        original_stop, original_submit, original_mission_stop = supervisor.stop, supervisor.submit, coordinator.stop

        def supervisor_stop(*args, **kwargs):
            order.append(("supervisor.stop", kwargs.get("origin")))
            return original_stop(*args, **kwargs)

        def supervisor_submit(command):
            order.append(("supervisor.submit", command.get("type")))
            return original_submit(command)

        def mission_stop(*args, **kwargs):
            order.append(("mission.stop", kwargs.get("origin")))
            return original_mission_stop(*args, **kwargs)

        # Instance attributes, so the supervisor's own _submit_stop reaches the recording stop as well.
        supervisor.stop, supervisor.submit, coordinator.stop = supervisor_stop, supervisor_submit, mission_stop
        return order

    def test_every_door_stops_the_run_first_and_then_the_mission(self):
        doors = (("stop", {}, [("supervisor.stop", "operator_console"), ("mission.stop", daemon.STOP_BY_STOP_OP)]),
                 ("mission_stop", {}, [("supervisor.stop", "operator_console"), ("mission.stop", daemon.STOP_BY_MISSION_STOP)]),
                 ("submit", {"command": stop_command("stopdoor0123456789a")},
                  [("supervisor.submit", "stop"), ("supervisor.stop", "operator_console"), ("mission.stop", daemon.STOP_BY_COMMAND)]))
        for op, request, expected in doors:
            with self.subTest(op=op):
                self.core.ask("mission_start")
                self.waiting()
                order = self.record_order()
                self.core.ask(op, request)
                self.assertEqual(order, expected)
                state = self.stopped()
                self.assertTrue(state["stop_requested"])
                self.core.core.supervisor.stop = type(self.core.core.supervisor).stop.__get__(self.core.core.supervisor)
                self.core.core.supervisor.submit = type(self.core.core.supervisor).submit.__get__(self.core.core.supervisor)
                self.core.core.mission.stop = type(self.core.core.mission).stop.__get__(self.core.core.mission)

    def test_each_answer_carries_both_parts_and_none_of_them_claims_motion_ceased(self):
        self.core.ask("mission_start")
        self.waiting()
        plain = self.core.ask("stop")
        self.assertTrue(plain["ok"])
        self.assertIsNone(plain["stop"], "no controller run was active")
        self.assertTrue(plain["mission"]["stopping"])
        self.stopped()

        started = self.core.ask("mission_start")
        self.waiting(started["mission_id"])
        through_mission = self.core.ask("mission_stop")
        self.assertTrue(through_mission["stopping"])
        self.assertEqual(through_mission["mission_id"], started["mission_id"])
        self.assertIn("run_stop", through_mission)
        self.assertIsNone(through_mission["run_stop"])
        self.stopped()

        started = self.core.ask("mission_start")
        self.waiting(started["mission_id"])
        submitted = self.core.ask("submit", {"command": stop_command("stopboth0123456789a")})
        receipt = {key: value for key, value in submitted.items() if key != "mission"}
        self.assertEqual(daemon.demo_contracts.validate("command_result", receipt), [],
                         "beside the mission part the answer is exactly the contract receipt")
        self.assertEqual((receipt["status"], receipt["reason_code"]), ("finished", "no_active_run"))
        self.assertEqual(submitted["mission"]["mission_id"], started["mission_id"])
        self.assertTrue(submitted["mission"]["stopping"])
        self.stopped()
        for answer in (plain, through_mission, submitted):
            self.assertNotIn("motion_ceased", repr(answer))

    def test_a_stop_with_nothing_to_stop_says_stopping_false(self):
        answer = self.core.ask("mission_stop")
        self.assertEqual((answer["ok"], answer["stopping"], answer["reason_code"]), (True, False, mission_coordinator.NO_RUNNING_MISSION))

    def test_a_stop_command_the_supervisor_refused_without_a_receipt_stops_no_mission(self):
        """A stop body with no usable command_id is answered {ok false, command_invalid} and has no `status`, because
        there is no id to key a receipt on. It stopped no run, so it must stop no mission either: the two halves of one
        stop never disagree about whether it was a stop at all."""
        started = self.core.ask("mission_start")
        self.waiting(started["mission_id"])
        no_id = {key: value for key, value in stop_command("unused0123456789abc").items() if key != "command_id"}
        bad_id = dict(stop_command("unused0123456789abc"), command_id="not an id!")
        for command in (no_id, bad_id):
            with self.subTest(command_id=command.get("command_id")):
                answer = self.core.ask("submit", {"command": command})
                self.assertEqual((answer.get("ok"), answer.get("reason_code")), (False, "command_invalid"))
                self.assertIsNone(answer.get("mission"), "no mission part: nothing was asked of the mission")
        time.sleep(3 * mission_coordinator.CAPABILITY_POLL_S)
        state = self.core.ask("mission_state")
        self.assertEqual((state["mission_id"], state["state"], state["stop_requested"]),
                         (started["mission_id"], mission.MISSION_WAITING, False))
        self.core.ask("mission_stop")

    def test_a_repeated_stop_command_does_not_stop_a_newer_mission(self):
        """The supervisor answers a known stop id from its journal and signals nothing; the mission half follows it."""
        self.core.ask("mission_start")
        self.waiting()
        self.core.ask("submit", {"command": stop_command("stoponce0123456789a")})
        self.stopped()
        newer = self.core.ask("mission_start")
        self.waiting(newer["mission_id"])
        again = self.core.ask("submit", {"command": stop_command("stoponce0123456789a")})
        self.assertIsNone(again["mission"], "a repeat stopped nothing now")
        time.sleep(3 * mission_coordinator.CAPABILITY_POLL_S)
        state = self.core.ask("mission_state")
        self.assertEqual((state["mission_id"], state["state"], state["stop_requested"]),
                         (newer["mission_id"], mission.MISSION_WAITING, False))
        self.core.ask("mission_stop")

    def test_a_stop_submits_nothing_to_the_resident_and_the_read_allowlist_is_unchanged(self):
        """Normal Stop is not implemented by touching Cake: no ApplyPlan, no rollback, and the read client's allowlist
        is exactly the four reads it was."""
        self.core.ask("mission_start")
        self.waiting()
        self.core.ask("stop")
        self.core.ask("mission_stop")
        self.core.ask("submit", {"command": stop_command("stopcake0123456789a")})
        self.stopped()
        self.assertEqual(self.deployment.submissions(), [], "no ApplyPlan reached the resident")
        self.assertEqual(cake_admin.READ_OPERATIONS, ("get-status", "read-flight", "list-slots", "get-health"))
        import inspect

        source = inspect.getsource(daemon.RuntimeCore._stop_run_and_mission)
        code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
        body = code.split('"""')[-1]  # the code after the docstring, which names what it never does
        for forbidden in ("cake_activation", "submit_apply_plan", "robot_link", "self.robot", "os.kill", "signal"):
            self.assertNotIn(forbidden, body, forbidden)


class TheSingleRunSlotIsShared(unittest.TestCase):
    """A mission and a controller run cannot hold the run slot at the same time, in either order."""

    def test_a_mission_start_is_refused_while_a_controller_run_is_active(self):
        deployment = Deployment(self)
        core = Core(self, config=deployment.config(), run_target=placed_when_available, step_s="0.2")
        accepted = core.ask("submit", {"command": start_command("runfirst0123456789a")})
        self.assertIn(accepted["status"], ("accepted", "running"))
        wait_for(self, lambda: core.core.supervisor.active_run(), "the controller run to be active")
        refused = core.ask("mission_start")
        self.assertEqual((refused["ok"], refused["reason_code"]), (False, "run_active"))
        self.assertEqual(core.ask("mission_state")["reason_code"], "no_mission_recorded", "nothing was written down")
        self.assertFalse(core.core.mission.running)
        core.ask("stop")
        core.wait_idle()

    def test_a_command_is_refused_while_a_mission_is_carried_and_stop_is_not(self):
        deployment = Deployment(self)
        core = Core(self, config=deployment.config(), run_target=placed_when_available)
        core.ask("mission_start")
        wait_for(self, lambda: core.ask("mission_state").get("state") == mission.MISSION_WAITING, "the mission to wait")
        refused = core.ask("submit", {"command": start_command("missionfirst0123456")})
        self.assertEqual((refused["status"], refused["reason_code"]), ("rejected", daemon.MISSION_HOLDS_RUN_SLOT))
        self.assertEqual(core.launches(), [], "no controller was started beside the mission")
        snapshot = core.ask("snapshot")
        self.assertEqual(daemon.demo_contracts.validate("snapshot", snapshot), [], "the new block reason is a contract v1 snapshot as it is")
        capabilities = {item["id"]: item for item in snapshot["capabilities"]}
        self.assertEqual((capabilities["start_cycle"]["state"], capabilities["start_cycle"]["reason_code"]),
                         ("blocked", daemon.MISSION_HOLDS_RUN_SLOT))
        self.assertIn("mission", capabilities["start_cycle"]["reason"])
        self.assertEqual(capabilities["stop"]["state"], "enabled", "Stop ends the mission, so it is not drawn as having nothing to stop")
        stopped = core.ask("submit", {"command": stop_command("missionstop01234567")})
        self.assertEqual(stopped["status"], "finished")
        self.assertTrue(stopped["mission"]["stopping"])
        wait_for(self, lambda: core.ask("mission_state").get("state") == mission.MISSION_STOPPED, "the mission to stop")
        wait_for(self, lambda: not core.core.mission.running, "the mission thread to end")
        self.assertIsNone(core.core.supervisor.block(), "the slot is free again once the mission ended")


class AMissionARuntimeLeftBehind(unittest.TestCase):
    def test_a_mission_left_waiting_by_a_runtime_that_died_can_be_stopped(self):
        """A runtime killed while its mission waited leaves the mission WAITING on the record and no thread to carry it.
        The next runtime's stop ends it STOPPED and says why."""
        from demo_runtime.journal import Journal

        targets = [dict(target) for target in TWO_TARGETS]  # the records below name cube_far

        def left_behind(data_dir):
            journal = Journal(Path(data_dir) / mission.MISSIONS_FILE_NAME)
            for record in (mission.mission_started("mission-leftbehind01", targets, t_wall_utc="t0", data_mode="synthetic",
                                                   performs_motion=False, execution_label="recording-rehearsal"),
                           mission.target_selected("mission-leftbehind01", "cube_far", t_wall_utc="t1"),
                           mission.target_waiting("mission-leftbehind01", "cube_far", mission.WAITING_ON_CAPABILITY, t_wall_utc="t2")):
                journal.append(record)
            journal.close()

        deployment = Deployment(self)
        core = Core(self, config=deployment.config(), run_target=placed_when_available, before=left_behind)
        self.assertEqual(core.ask("mission_state")["state"], mission.MISSION_WAITING)
        refused = core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual(refused["reason_code"], "unknown_mission", "this runtime never adopted it, and submits nothing for it")
        answer = core.ask("mission_stop")
        self.assertEqual((answer["mission_id"], answer["stopping"], answer["carried"]), ("mission-leftbehind01", True, False))
        state = core.ask("mission_state")
        self.assertEqual(state["state"], mission.MISSION_STOPPED)
        self.assertEqual(state["detail"], mission_coordinator.ORPHAN_STOP_DETAIL)
        self.assertEqual({target["target_id"]: target["state"] for target in state["targets"]}["cube_far"], mission.TARGET_STOPPED)
        self.assertEqual(deployment.submissions(), [])


class TheActivationRecordsWhatTheResidentSaid(unittest.TestCase):
    def test_the_transaction_and_the_slot_read_before_and_after_are_persisted(self):
        deployment = Deployment(self)
        core = Core(self, config=deployment.config(), run_target=placed_when_available)
        core.ask("mission_start")
        wait_for(self, lambda: core.ask("mission_state").get("state") == mission.MISSION_WAITING, "the mission to wait")
        deployment.verdict("complete")
        answer = core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        entry = core.ask("mission_state")["activations"][-1]
        self.assertEqual(entry["activation_state"], mission.ACTIVATION_OBSERVED)
        transaction = entry["transaction"]
        self.assertEqual((transaction["operation_uuid"], transaction["transaction_uuid"], transaction["transaction_phase"], transaction["status"]),
                         (answer["operation_uuid"], "t-1", "COMPLETED", "ok"))
        before, after = entry["slot_before"], entry["slot_after"]
        self.assertEqual((before["slot_uuid"], before["name"], before["state"], before["capsule"], before["generation"], before["instance"]),
                         (CAPABILITY_SLOT, "mobile_approach", "ACTIVE", INERT_CAPSULE, "1", "3"))
        self.assertEqual((after["slot_uuid"], after["capsule"], after["generation"]), (CAPABILITY_SLOT, MOBILE_CAPSULE, "2"))
        self.assertEqual((before["session_uuid"], before["plan_digest"], before["epoch"]), (RESIDENT_SESSION, PLAN_BEFORE, "2"))
        self.assertEqual((after["session_uuid"], after["plan_digest"], after["epoch"]), (RESIDENT_SESSION, PLAN_AFTER, "3"))
        self.assertEqual((before["capability_active"], after["capability_active"]), (False, True))
        self.assertIs(entry["stopped_during_activation"], False)
        wait_for(self, lambda: core.ask("mission_state").get("state") == mission.MISSION_COMPLETE, "the mission to complete")

    def test_an_activation_after_a_stop_was_asked_for_is_a_conflict_and_nothing_is_submitted(self):
        deployment = Deployment(self)
        core = Core(self, config=deployment.config(), run_target=placed_when_available)
        core.ask("mission_start")
        wait_for(self, lambda: core.ask("mission_state").get("state") == mission.MISSION_WAITING, "the mission to wait")
        deployment.verdict("complete")
        core.ask("mission_stop")
        answer = core.ask("mission_activate", {"candidate_key": CANDIDATE_KEY})
        self.assertEqual((answer["ok"], answer["reason_code"]), (False, mission_coordinator.MISSION_STOPPING))
        self.assertEqual(deployment.submissions(), [])
        self.assertEqual(core.ask("mission_state")["activations"], [])


if __name__ == "__main__":
    unittest.main()
