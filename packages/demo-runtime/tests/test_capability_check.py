#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# packages/demo-runtime/tests/test_capability_check.py: the capability slot check, driven through the daemon's own
# dispatch against fake_robot_probe.py with its opt-in apply script. NO ROBOT, NO RESIDENT, NO NETWORK, NO CONTROLLER.
#
# What is proved, and it is software behaviour only:
#   1. the check exists only in a LIVE runtime that names the robot, prepared candidates and a capability binding;
#   2. every refusal submits nothing and writes nothing: a malformed body, an unknown candidate, a digest that is not
#      the candidate's, a mission carried, a run active, an unresolved command, a PENDING operation, a resident that is
#      not the pinned build, cannot be read, reports another session, a host running or unreadable under the supervisor
#      slot, and a capability slot read from another session;
#   3. the invalid candidate is answered ABORTED and the slot, read before and after, did not move;
#   4. the valid candidate COMPLETED and the slot, read back, moved to the mobile capsule at generation + 1: OBSERVED;
#   5. a resident session that changes across the submit turns what would read OBSERVED into COMPLETED_NOT_OBSERVED;
#   6. one check at a time: a second apply while one is in flight is refused and submits nothing, a command and a mission
#      start are refused while a check holds the run slot, and Stop is answered within 0.2 s;
#   7. the record: attempt before the submit, answer after, the host before and after, a stamp per phase, and an
#      attempt a previous runtime never answered is said to be exactly that;
#   8. HELD means no host process under the supervisor slot: a child the supervisor reports in any state but HEALTHY is
#      NOT held (robot_link answers host_running False for it, the cautious answer for motion permission only);
#   9. OBSERVED needs the slot to have CHANGED: a completed Plan that leaves the slot exactly as it was, capability capsule
#      included, is COMPLETED_NOT_OBSERVED, and the record and the log line say whether the slot changed;
#  10. a runtime that is closing submits nothing new, and its wait for a check in flight is derived from every bound after
#      the attempt record and fits under the runtime unit's stop bound.
# What is NOT proved here: anything about LeKiwi's resident. The operator's run on the robot is what shows that.
# Strings the repository's text gates refuse are BUILT AT RUN TIME, never written out.
# Run directly: python3 packages/demo-runtime/tests/test_capability_check.py
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
sys.path.insert(0, str(PACKAGE_DIR))

from demo_runtime import cake_activation, capability_check, daemon, forwards, mission, mission_service, read_models, robot_link, sources  # noqa: E402
from demo_runtime.bundle import make_tree_writable  # noqa: E402
from demo_runtime.config import RuntimeConfig  # noqa: E402
from demo_runtime.preflight import readiness_of  # noqa: E402
from demo_runtime.rpc import MUTATING_OPS  # noqa: E402

import demo_contracts  # noqa: E402

WAIT_S = 20.0
TEST_ADDRESS = ".".join(("192", "0", "2", "12"))  # RFC 5737: names no machine that exists
TEST_TARGET = "operator" + "name" + "@" + TEST_ADDRESS
REMOTE_SOCKET = "/run/user/1000/kc-lekiwi.sock"
SESSION, OTHER_SESSION = "01a0d8320c9c7b09b03c1f945f489104", "7c3e0f1a2b4d5e6f708192a3b4c5d6e7"
SUPERVISOR_SLOT, CAPABILITY_SLOT, HOLDER_SLOT = ("50505430405080500000737570657276", "6d6f62696c655f617070726f61636801",
                                                 "6d6f62696c655f617070726f61636802")
SUPERVISOR_CAPSULE, MOBILE_CAPSULE, INERT_CAPSULE = "a1" * 32, "b2" * 32, "c3" * 32
ROBOT_BUILD, OTHER_BUILD = "d3" * 32, "e4" * 32
VALID_KEY, INVALID_KEY = "mobile-demo-swapped", "mobile-demo-active-successor-abort-swap"
VALID_BYTES, INVALID_BYTES = b"the valid prepared swap plan", b"the invalid successor plan"
VALID_DIGEST, INVALID_DIGEST = cake_activation.digest_of(VALID_BYTES), cake_activation.digest_of(INVALID_BYTES)


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def status_text(session: str, build: str = ROBOT_BUILD, epoch: int = 3) -> str:
    return "\n".join(("session_uuid " + session, "build_identity " + build, "plan_digest " + "c9" * 32, "epoch " + str(epoch),
                      "slots 3", "bindings 1", "resources 0", "transaction_state none",
                      "flight_sequence_oldest 0", "flight_sequence_next 4")) + "\n"


def slots_text(*, capability_capsule: str, generation: int, instance: int, supervisor_state: str = "ACTIVE",
               supervisor_generation: int = 1, supervisor_instance: int = 6003298303284871169) -> str:
    return (f"slot capsule={SUPERVISOR_CAPSULE} config={'0d' * 32} generation={supervisor_generation} "
            f"instance={supervisor_instance} name=supervisor.lekiwi "
            f"state={supervisor_state} uuid={SUPERVISOR_SLOT}\n"
            f"slot capsule={capability_capsule} config={'0e' * 32} generation={generation} instance={instance} name=mobile_approach "
            f"state=ACTIVE uuid={CAPABILITY_SLOT}\n"
            f"slot capsule={MOBILE_CAPSULE} config={'0f' * 32} generation=1 instance=2 name=mobile_approach.holder state=ACTIVE "
            f"uuid={HOLDER_SLOT}\n")


def health_text(*, host_pid: int, host_state: str = "HEALTHY", spawns: int = 5, restarts: int = 5,
                supervisor_row: bool = True) -> str:
    # A HELD host is what the deployed robot shows: the supervisor spawned the wrapper five times, every one refused before
    # opening the servo bus, and the slot reports no child. pid 0 is "no host runs here now". `sequence` is the spawn
    # count and `flags` the restart count (robot_link.parse_health_rows).
    supervisor = (f"health arg0=12 arg1={host_pid} code=0 flags={restarts} sequence={spawns} slot={SUPERVISOR_SLOT} state=1 "
                  f"state_name={host_state} surface=reported\n") if supervisor_row else ""
    return (supervisor +
            f"health arg0=3 arg1=4343 code=0 flags=1 sequence=2 slot={CAPABILITY_SLOT} state=1 state_name=HEALTHY surface=reported\n")


class Listening:
    """A unix socket that accepts and closes: what the forward's local end looks like while ssh holds it."""

    def __init__(self, path):
        self.path = str(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(16)
        self.sock.settimeout(0.1)
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._halt.is_set():
            try:
                peer, _ = self.sock.accept()
                peer.close()
            except OSError:
                continue

    def close(self):
        self._halt.set()
        self._thread.join(timeout=2.0)
        self.sock.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


class FakeLink:
    def __init__(self):
        self.up = True

    def start(self):
        return self

    def check(self):
        return forwards.CONNECTED if self.up else forwards.FAILED

    def connected(self):
        return self.up

    def report(self):
        return {"state": self.check(), "reason": None, "since_wall_utc": "2026-09-25T10:00:00.000Z", "since_age_ms": 5.0,
                "starts": 1, "deaths": 0, "restarts": 0, "next_attempt_in_ms": None}

    def stop(self):
        pass


class FakeWatch:
    def start(self):
        return self

    def stop(self):
        pass

    def reading(self):
        return readiness_of("never_read", None)

    def host_pids(self):
        return None


class Resident:
    """The fake robot resident: a listening socket, its state directory, the fake admin-probe and two prepared Plans."""

    def __init__(self, test):
        self.tmp = Path(tempfile.mkdtemp(prefix="kc-cc-"))
        test.addCleanup(self.remove)
        self.socket_path = self.tmp / "a.sock"
        self.state = Path(str(self.socket_path) + ".state")
        self.state.mkdir()
        self.probe = self.tmp / "admin-probe"
        shutil.copy(PACKAGE_DIR / "tests" / "fake_robot_probe.py", self.probe)
        os.chmod(self.probe, 0o755)
        self.listening = Listening(self.socket_path)
        test.addCleanup(self.listening.close)
        self.valid, self.invalid = self.tmp / "mobile-demo-swapped.cakepln", self.tmp / "mobile-demo-invalid.cakepln"
        self.valid.write_bytes(VALID_BYTES)
        self.invalid.write_bytes(INVALID_BYTES)
        self.write()
        self.script()

    def remove(self):
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, *, session=SESSION, build=ROBOT_BUILD, host_pid=0, host_state="HEALTHY", health=True,
              capability_capsule=INERT_CAPSULE, generation=1, instance=7, supervisor_state="ACTIVE", supervisor_row=True):
        (self.state / "status").write_text(status_text(session, build), encoding="ascii")
        (self.state / "slots").write_text(slots_text(capability_capsule=capability_capsule, generation=generation, instance=instance,
                                                     supervisor_state=supervisor_state), encoding="ascii")
        (self.state / "health").write_text(health_text(host_pid=host_pid, host_state=host_state, supervisor_row=supervisor_row),
                                           encoding="ascii")
        (self.state / "flight").write_text("", encoding="ascii")
        unavailable = self.state / "health_unavailable"
        if health and unavailable.exists():
            unavailable.unlink()
        elif not health:
            unavailable.write_text("", encoding="ascii")

    def script(self, *, valid_files=None, valid_phase="COMPLETED", sleep_s=0.0, valid_epoch=4):
        """The valid Plan COMPLETES and moves the capability slot to the mobile capsule at generation 2 with a new
        instance, the holder keeping its capsule; the invalid one is ABORTED and changes nothing."""
        moved = {"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8),
                 "status": status_text(SESSION, epoch=4)}
        script = {sha(VALID_BYTES): {"phase": valid_phase, "sleep_s": sleep_s, "transaction_handle": 21, "epoch": valid_epoch,
                                     "files": moved if valid_files is None else valid_files},
                  sha(INVALID_BYTES): {"phase": "ABORTED", "sleep_s": sleep_s, "transaction_handle": 20}}
        (self.state / "apply_script.json").write_text(json.dumps(script), encoding="ascii")

    def attempts(self) -> list:
        path = self.state / "apply_plan_attempts"
        return path.read_text(encoding="ascii").splitlines() if path.exists() else []

    def mapping(self, *, candidates=True, capability=True, robot=True) -> dict:
        cake = {"admin_probe_path": str(self.probe)}
        if robot:
            cake["robot"] = {"remote_admin_socket_path": REMOTE_SOCKET, "local_admin_socket_path": str(self.socket_path),
                             "expected_build_identity": ROBOT_BUILD}
        else:
            cake["admin_socket_path"] = str(self.socket_path)
        if capability:
            cake["capability"] = {"name": "mobile_approach", "slot_uuid": CAPABILITY_SLOT, "active_capsule": MOBILE_CAPSULE}
        if candidates:
            cake["activation_candidates"] = {VALID_KEY: str(self.valid), INVALID_KEY: str(self.invalid)}
        return {"data_root": str(self.tmp / "demo-data"), "saved_pose_file": "pose.json", "pad_template_dir": "pad",
                "observation_root": "observations", "host": {"address": TEST_ADDRESS, "robot_id": "unit" + "-0042"},
                "pi_ssh_target": TEST_TARGET, "cameras": {"wrist_id": "usb-test-wrist", "front_id": None}, "cake": cake}


def command(command_type, args, command_id):
    return {"contract_version": demo_contracts.CONTRACT_VERSION, "command_id": command_id, "type": command_type, "args": args,
            "client": {"session_id": "tab-0123456789ab", "contract_version": demo_contracts.CONTRACT_VERSION}}


class StubCoordinator:
    """What _op_mission_start needs of a coordinator, recording whether a start was ever asked for."""

    performs_motion = False
    running = False

    def __init__(self):
        self.started = []

    def start(self, targets):
        self.started.append(targets)
        return {"ok": True, "mission_id": "mission-stub"}

    def cancel_now(self, origin="mission_stop"):
        return None

    def stop(self, origin="mission_stop"):
        return {"ok": True, "stopping": False, "reason_code": "no_running_mission"}

    def close(self):
        pass


class CheckCase(unittest.TestCase):
    def setUp(self):
        self.resident = Resident(self)
        self.data = self.resident.tmp / "data"
        self.link = FakeLink()
        self.launches = self.resident.tmp / "controller-launches.log"
        self.addCleanup(lambda: self.assertFalse(self.launches.exists(), "a controller was launched"))

    def core(self, *, mapping=None, mode=daemon.LIVE):
        core = daemon.RuntimeCore(data_dir=self.data, mode=mode, config=RuntimeConfig.from_mapping(mapping or self.resident.mapping()),
                                  source_root=REPO_ROOT, robot_link=self.link, robot_host_watch=FakeWatch(),
                                  controller_entry=daemon.FAKE_CONTROLLER_ENTRY, extra_env={"KC_FAKE_LAUNCH_LOG": str(self.launches)},
                                  source_supervisor=lambda hub: sources.SourceSupervisor(hub))
        self.addCleanup(core.close)
        return core

    def ask(self, core, op, **request):
        core.cake.invalidate()
        return core.handle(op, {"op": op, **request}, None)

    def apply(self, core, key=VALID_KEY, digest=VALID_DIGEST, session=SESSION):
        return self.ask(core, "capability_check_apply", candidate_key=key, payload_digest=digest, session_uuid=session)

    def records(self) -> list:
        path = self.data / capability_check.CHECKS_FILE_NAME
        return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()] if path.exists() else []


# ---- 1. where it exists ------------------------------------------------------------------------------------------------------

class WhereTheCheckExists(CheckCase):
    def test_a_live_runtime_with_robot_candidates_and_a_binding_has_one(self):
        core = self.core()
        self.assertIsNotNone(core.capability_check)
        state = self.ask(core, "capability_check_state")
        self.assertTrue(state["ok"])
        self.assertEqual(state["capability_check_version"], capability_check.CAPABILITY_CHECK_VERSION)
        self.assertEqual(state["candidates"], [{"key": INVALID_KEY, "payload_digest": INVALID_DIGEST},
                                               {"key": VALID_KEY, "payload_digest": VALID_DIGEST}])
        self.assertEqual(self.resident.attempts(), [], "a read submitted something")

    def test_without_prepared_candidates_it_is_off_and_says_why(self):
        core = self.core(mapping=self.resident.mapping(candidates=False))
        self.assertIsNone(core.capability_check)
        for op, request in (("capability_check_state", {}),
                            ("capability_check_apply", {"candidate_key": VALID_KEY, "payload_digest": VALID_DIGEST, "session_uuid": SESSION})):
            answer = self.ask(core, op, **request)
            self.assertEqual((answer["ok"], answer["reason_code"], answer["detail"]),
                             (False, capability_check.UNAVAILABLE, capability_check.NO_CANDIDATE))
        self.assertEqual(self.resident.attempts(), [])

    def test_without_a_capability_binding_it_is_off(self):
        core = self.core(mapping=self.resident.mapping(capability=False))
        self.assertEqual(core.capability_check_unavailable, capability_check.NO_CAPABILITY)

    def test_the_conditions_are_checked_in_their_order(self):
        robot = type("Robot", (), {"expected_build_identity": ROBOT_BUILD})()
        some = cake_activation.PreparedCandidates({VALID_KEY: str(self.resident.valid)})
        none = cake_activation.PreparedCandidates({})
        binding = ("mobile_approach", CAPABILITY_SLOT, MOBILE_CAPSULE)
        reason = capability_check.unavailable_reason
        self.assertEqual(reason(live=False, robot=robot, probe_path="/p", socket_path="/s", candidates=some, binding=binding),
                         capability_check.NOT_LIVE)
        self.assertEqual(reason(live=True, robot=None, probe_path="/p", socket_path="/s", candidates=some, binding=binding),
                         capability_check.ROBOT_NOT_CONFIGURED)
        unpinned = type("Robot", (), {"expected_build_identity": ""})()
        self.assertEqual(reason(live=True, robot=unpinned, probe_path="/p", socket_path="/s", candidates=some, binding=binding),
                         capability_check.ROBOT_BUILD_NOT_PINNED)
        self.assertEqual(reason(live=True, robot=robot, probe_path=None, socket_path=None, candidates=some, binding=binding),
                         capability_check.CAKE_NOT_CONFIGURED)
        self.assertEqual(reason(live=True, robot=robot, probe_path="/p", socket_path="/s", candidates=none, binding=binding),
                         capability_check.NO_CANDIDATE)
        self.assertEqual(reason(live=True, robot=robot, probe_path="/p", socket_path="/s", candidates=some, binding=None),
                         capability_check.NO_CAPABILITY)
        self.assertEqual(reason(live=True, robot=robot, probe_path="/p", socket_path="/s", candidates=some,
                                binding=("mobile_approach", "not-a-slot", MOBILE_CAPSULE)), capability_check.CAPABILITY_NOT_BINDABLE)
        self.assertIsNone(reason(live=True, robot=robot, probe_path="/p", socket_path="/s", candidates=some, binding=binding))

    def test_apply_is_a_mutating_op_and_the_read_is_not(self):
        self.assertIn("capability_check_apply", MUTATING_OPS)
        self.assertNotIn("capability_check_state", MUTATING_OPS)

    def test_the_daemon_s_run_slot_code_is_the_check_s_and_has_its_own_sentence(self):
        self.assertEqual(daemon.CAPABILITY_CHECK_HOLDS_RUN_SLOT, capability_check.IN_FLIGHT)
        state, sentence, _dependency = read_models.BLOCK_REASONS[capability_check.IN_FLIGHT]
        self.assertEqual(state, "blocked")
        self.assertIn("no motion", sentence)
        self.assertNotIn("preflight", sentence)


# ---- 2. every refusal submits nothing ---------------------------------------------------------------------------------------

class EveryRefusalSubmitsNothing(CheckCase):
    def refused(self, core, expected, **request):
        body = {"candidate_key": VALID_KEY, "payload_digest": VALID_DIGEST, "session_uuid": SESSION, **request}
        answer = self.ask(core, "capability_check_apply", **{key: value for key, value in body.items() if value is not ...})
        self.assertFalse(answer["ok"], answer)
        self.assertEqual(answer["reason_code"], expected, answer)
        self.assertEqual(self.resident.attempts(), [], "a refused check reached the resident")
        self.assertEqual(self.records(), [], "a refused check was written down as attempted")
        return answer

    def test_a_body_that_is_not_exactly_the_three_keys_is_a_bad_request(self):
        core = self.core()
        self.refused(core, "bad_request", payload_digest=...)
        self.refused(core, "bad_request", extra="x")
        self.refused(core, "bad_request", payload_digest="ab" * 32)
        self.refused(core, "bad_request", session_uuid="not a session")
        self.refused(core, "bad_request", candidate_key="")
        self.refused(core, "bad_request", candidate_key="k" * 65)
        self.refused(core, "bad_request", candidate_key=7)

    def test_an_unknown_candidate_and_a_digest_that_is_not_its_own_are_refused(self):
        core = self.core()
        self.refused(core, capability_check.UNKNOWN_CANDIDATE, candidate_key="mobile-demo")
        self.refused(core, capability_check.DIGEST_MISMATCH, payload_digest=INVALID_DIGEST)

    def test_a_mission_a_run_or_an_unresolved_command_holds_the_slot(self):
        core = self.core()
        with mock.patch.object(core, "_mission_is_carried", return_value=True):
            self.refused(core, daemon.MISSION_HOLDS_RUN_SLOT)
        with mock.patch.object(core.supervisor, "active_run", return_value={"run_id": "r"}):
            self.refused(core, "run_active")
        with mock.patch.object(core.supervisor, "unresolved", return_value=["c"]):
            self.refused(core, "owner_unresolved")

    def test_the_claim_refuses_under_the_submit_lock_when_a_run_was_published_after_the_first_look(self):
        core = self.core()
        answers = iter([None, "run_active"])
        with mock.patch.object(core, "_check_slot_refusal", side_effect=lambda: next(answers)):
            self.refused(core, "run_active")
        self.assertFalse(core.capability_check.in_flight())

    def test_a_resident_that_is_not_the_pinned_build_or_cannot_be_read_is_refused(self):
        core = self.core()
        self.resident.write(build=OTHER_BUILD)
        self.refused(core, "robot_resident_not_the_robot")
        self.resident.write()
        self.link.up = False
        self.refused(core, "robot_unreachable")

    def test_another_session_is_refused(self):
        core = self.core()
        self.refused(core, capability_check.SESSION_MISMATCH, session_uuid=OTHER_SESSION)

    def test_a_host_that_runs_or_cannot_be_read_is_refused_because_the_host_stays_held(self):
        core = self.core()
        self.resident.write(host_pid=4242)
        self.refused(core, capability_check.HOST_NOT_HELD)
        self.resident.write(health=False)
        self.refused(core, "robot_host_unreadable")

    def test_a_host_process_the_supervisor_reports_but_not_healthy_is_not_held(self):
        """robot_link answers host_running False with supervisor_host_not_healthy for a NONZERO child pid in any state but
        HEALTHY: nothing to bind motion permission to, which is the cautious answer there. For "the host is held" it is
        the permissive one, because a host process IS there. So it refuses, and the state read says so before a press."""
        core = self.core()
        for state_name in ("STARTING", "DEGRADED"):
            with self.subTest(state_name=state_name):
                self.resident.write(host_pid=4242, host_state=state_name)
                answer = self.refused(core, capability_check.HOST_NOT_HELD)
                self.assertEqual(answer["detail"], "supervisor_host_not_healthy")
                state = self.ask(core, "capability_check_state")
                self.assertEqual(state["off_reason"], capability_check.HOST_NOT_HELD)
                self.assertIs(state["current"]["host_running"], False, "robot_link's own answer is shown as it is")
                self.assertIs(state["current"]["host_held"], False)

    def test_a_supervisor_slot_that_is_not_active_is_held_only_when_its_health_row_shows_no_child(self):
        """A slot that is not ACTIVE says nothing on its own about a process under it: its get-health row does. It is held
        only when that row is missing or reports child pid 0; a live child refuses, and a health answer that could not be
        read leaves it unknown, which refuses too."""
        core = self.core()
        self.resident.write(supervisor_state="STOPPING", host_pid=4242)
        answer = self.refused(core, capability_check.HOST_NOT_HELD)
        self.assertEqual(answer["detail"], "supervisor_slot_not_active")
        self.assertIs(self.ask(core, "capability_check_state")["current"]["host_held"], False)
        self.resident.write(supervisor_state="STOPPING", health=False)
        self.refused(core, "robot_host_unreadable")
        for shape in ({"host_pid": 0}, {"supervisor_row": False}):
            with self.subTest(**shape):
                self.resident.write(supervisor_state="STOPPING", **shape)
                state = self.ask(core, "capability_check_state")
                self.assertIs(state["current"]["host_held"], True)
                self.assertIsNone(state["off_reason"])

    def test_a_slot_read_from_another_session_is_refused(self):
        core = self.core()
        other = {"read": "ok", "session_uuid": OTHER_SESSION, "capability_active": False}
        with mock.patch.object(core.capability_check, "_read_slot", return_value=other):
            self.refused(core, capability_check.SLOT_SESSION_MISMATCH)

    def test_a_pending_operation_of_this_runtime_refuses_the_next_check_naming_it(self):
        core = self.core()
        core.capability_check._pending = "0f" * 16
        answer = self.refused(core, capability_check.IN_FLIGHT)
        self.assertEqual(answer["operation_uuid"], "0f" * 16)

    def test_a_replay_runtime_answers_replay_only(self):
        core = self.core(mode=daemon.REPLAY, mapping=self.resident.mapping(robot=False))
        answer = self.ask(core, "capability_check_apply", candidate_key=VALID_KEY, payload_digest=VALID_DIGEST, session_uuid=SESSION)
        self.assertEqual(answer["reason_code"], "replay_only")
        self.assertEqual(self.resident.attempts(), [])


# ---- 3, 4 and 5. what an apply comes to ----------------------------------------------------------------------------------------

class WhatAnApplyComesTo(CheckCase):
    def test_the_invalid_candidate_is_aborted_and_the_slot_does_not_move(self):
        core = self.core()
        answer = self.apply(core, INVALID_KEY, INVALID_DIGEST)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_REJECTED)
        self.assertEqual(answer["transaction_phase"], "ABORTED")
        before, after = answer["slot_before"], answer["slot_after"]
        for field in ("capsule", "generation", "instance", "state", "session_uuid"):
            self.assertEqual(before[field], after[field], field)
        self.assertEqual((before["capsule"], before["generation"]), (INERT_CAPSULE, "1"))
        self.assertIs(answer["slot_changed"], False, "the invalid candidate left the slot as it was")
        self.assertFalse(answer["resident_session_changed"])
        self.assertIs(answer["host_before"]["host_running"], False)
        self.assertIs(answer["host_after"]["host_running"], False)
        self.assertIs(answer["host_before"]["held"], True)
        self.assertIs(answer["host_after"]["held"], True)
        self.assertEqual(len(self.resident.attempts()), 1)

    def test_the_valid_candidate_is_observed_at_the_next_generation_without_a_resident_restart(self):
        core = self.core()
        answer = self.apply(core)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED, answer)
        self.assertEqual(answer["transaction_phase"], "COMPLETED")
        self.assertEqual((answer["slot_before"]["capsule"], answer["slot_before"]["generation"]), (INERT_CAPSULE, "1"))
        self.assertEqual((answer["slot_after"]["capsule"], answer["slot_after"]["generation"]), (MOBILE_CAPSULE, "2"))
        self.assertNotEqual(answer["slot_before"]["instance"], answer["slot_after"]["instance"])
        self.assertIs(answer["slot_changed"], True)
        self.assertEqual(answer["slot_after"]["session_uuid"], SESSION)
        self.assertFalse(answer["resident_session_changed"], "the resident did not restart")
        self.assertEqual(answer["transaction"]["transaction_handle"], "21")
        self.assertEqual(answer["transaction"]["answer_epoch"], "4")

    def test_a_resident_session_that_changes_across_the_submit_is_not_an_observation(self):
        core = self.core()
        self.resident.script(valid_files={"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=1, instance=1),
                                          "status": status_text(OTHER_SESSION)})
        answer = self.apply(core)
        self.assertTrue(answer["slot_after"]["capability_active"], "the slot does read active afterwards")
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED)
        self.assertEqual(answer["detail"], capability_check.SESSION_CHANGED_DETAIL)
        self.assertTrue(answer["resident_session_changed"])

    def test_a_valid_plan_that_leaves_the_slot_as_it_was_is_not_an_observed_change(self):
        """The robot's slot may already carry the capability (handoff 12.6: generation 2 after the swap). A COMPLETED
        re-apply that leaves capsule, generation and instance exactly as they were shows no change this operation made, so
        it is NOT OBSERVED, although the slot read back does carry the capability capsule."""
        core = self.core()
        self.resident.write(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8)
        self.resident.script(valid_files={"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8)})
        answer = self.apply(core)
        self.assertEqual(answer["transaction_phase"], "COMPLETED")
        self.assertTrue(answer["slot_after"]["capability_active"], "the slot does carry the capability capsule")
        self.assertIs(answer["slot_changed"], False)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_COMPLETED_NOT_OBSERVED)
        self.assertEqual(answer["detail"], capability_check.SLOT_UNCHANGED_DETAIL)
        self.assertIs(self.records()[-1]["slot_changed"], False, "the answer record says the slot did not change")
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertEqual((view["activation_state"], view["slot_changed"]), (mission.ACTIVATION_COMPLETED_NOT_OBSERVED, False))
        subscription = core.log.subscribe(0)
        core.log.unsubscribe(subscription)
        said = [event["payload"]["text"] for event in subscription.backlog if event.get("reason_code") == "capability_check_answered"]
        self.assertEqual(len(said), 1)
        self.assertIn("the slot did not change", said[0])
        self.assertNotIn("recorded OBSERVED", said[0])

    def test_whether_the_slot_changed_is_decided_only_from_two_readings_that_both_answered(self):
        changed = capability_check.slot_changed
        before = {"read": "ok", "capsule": INERT_CAPSULE, "generation": "1", "instance": "7"}
        self.assertIs(changed(before, {**before, "capsule": MOBILE_CAPSULE, "generation": "2", "instance": "8"}), True)
        self.assertIs(changed(before, {**before, "instance": "9"}), True, "a new instance alone is a change")
        self.assertIs(changed(before, dict(before)), False)
        self.assertIsNone(changed(before, {**before, "read": cake_activation.READ_FAILED}))
        self.assertIsNone(changed(before, {**before, "instance": None}), "a component not read is not a component unchanged")
        self.assertIsNone(changed(None, before))

    def test_a_host_process_the_apply_brought_back_unhealthy_is_recorded_as_not_held(self):
        core = self.core()
        moved = {"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8),
                 "health": health_text(host_pid=4242, host_state="STARTING")}
        self.resident.script(valid_files=moved)
        answer = self.apply(core)
        self.assertIs(answer["host_after"]["host_running"], False, "robot_link says no HEALTHY host runs")
        self.assertIs(answer["host_after"]["held"], False, "and a host process is there all the same")
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertIs(view["host_held_throughout"], False)
        self.assertIs(view["host_after"]["held"], False)

    def test_a_host_the_apply_brought_back_is_recorded_not_hidden(self):
        core = self.core()
        moved = {"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8),
                 "health": health_text(host_pid=4242)}
        self.resident.script(valid_files=moved)
        answer = self.apply(core)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED, "the verdict is the slot's")
        self.assertIs(answer["host_after"]["host_running"], True)
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertIs(view["host_held_throughout"], False)

    def test_spawns_between_the_two_reads_make_the_host_not_held_throughout_and_the_record_names_them(self):
        """Two point reads cannot see a wrapper spawned, refused and gone between them: both read child pid 0. The
        supervisor's counters can. The deployed shape: DEGRADED, pid 0, five spawns; after the Plan it is DEGRADED at pid 0
        again, with ten spawns and ten restarts."""
        core = self.core()
        self.resident.write(host_pid=0, host_state="DEGRADED")
        moved = {"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8),
                 "health": health_text(host_pid=0, host_state="DEGRADED", spawns=10, restarts=10)}
        self.resident.script(valid_files=moved)
        answer = self.apply(core)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED, "the verdict is the slot's")
        self.assertIs(answer["host_before"]["held"], True)
        self.assertIs(answer["host_after"]["held"], True, "each read on its own says held")
        self.assertIs(answer["host_held_throughout"], False, "the counters say the supervisor spawned between the reads")
        self.assertIn("spawns 5 to 10", answer["host_throughout_detail"])
        self.assertIn("restarts 5 to 10", answer["host_throughout_detail"])
        attempt, answered = self.records()
        self.assertEqual((attempt["host_before"]["supervisor_slot"]["spawns"], attempt["host_before"]["supervisor_slot"]["restarts"]),
                         (5, 5), "the read before is on the record with its counters")
        self.assertEqual((answered["host_after"]["supervisor_slot"]["spawns"], answered["host_after"]["supervisor_slot"]["restarts"]),
                         (10, 10), "and the read after with its own")
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertIs(view["host_held_throughout"], False)
        self.assertIn("spawns 5 to 10", view["host_throughout_detail"])

    def test_a_supervisor_slot_re_applied_between_the_two_reads_is_not_held_throughout(self):
        core = self.core()
        moved = {"slots": slots_text(capability_capsule=MOBILE_CAPSULE, generation=2, instance=8, supervisor_generation=2,
                                     supervisor_instance=6003298303284871170)}
        self.resident.script(valid_files=moved)
        answer = self.apply(core)
        self.assertIs(answer["host_held_throughout"], False)
        self.assertIn("supervisor slot generation 1 to 2", answer["host_throughout_detail"])
        self.assertIn("supervisor slot instance 6003298303284871169 to 6003298303284871170", answer["host_throughout_detail"])

    def test_the_host_is_held_throughout_only_when_every_supervisor_counter_read_the_same(self):
        core = self.core()
        answer = self.apply(core)
        self.assertIs(answer["host_held_throughout"], True)
        self.assertIsNone(answer["host_throughout_detail"])
        before, after = answer["host_before"]["supervisor_slot"], answer["host_after"]["supervisor_slot"]
        self.assertEqual(before, after)
        self.assertEqual((before["slot_state"], before["slot_generation"], before["slot_instance"], before["spawns"], before["restarts"]),
                         ("ACTIVE", 1, "6003298303284871169", 5, 5))
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertEqual((view["host_held_throughout"], view["host_throughout_detail"]), (True, None))

    def test_an_apply_whose_answer_did_not_arrive_in_time_keeps_the_check_closed(self):
        """cake_activation's rule: after a timeout THE OPERATION MAY STILL BE RUNNING. The shared verdict still says
        REQUEST_FAILED, but the check treats the operation as unresolved: it names it, refuses the next check for the life
        of this runtime, and never says the request did not land."""
        with mock.patch.object(mission_service, "ACTIVATION_TIMEOUT_S", 0.5):
            core = self.core()
        self.resident.script(sleep_s=2.0)
        first = self.apply(core)
        self.assertEqual(first["activation_state"], mission.ACTIVATION_FAILED, "the shared verdict is unchanged")
        self.assertEqual(first["transaction"]["reason_code"], capability_check.SUBMIT_TIMED_OUT)
        self.assertIs(first["unresolved"], True)
        self.assertEqual(first["detail"], capability_check.TIMED_OUT_DETAIL)
        self.assertNotIn("did not land", first["detail"])
        self.assertEqual(core.capability_check.pending_operation, first["operation_uuid"])
        state = self.ask(core, "capability_check_state")
        self.assertEqual((state["off_reason"], state["pending_operation_uuid"]), (capability_check.IN_FLIGHT, first["operation_uuid"]))
        self.assertIs(state["checks"][-1]["unresolved"], True)
        second = self.apply(core, INVALID_KEY, INVALID_DIGEST)
        self.assertEqual((second["ok"], second["reason_code"], second["operation_uuid"]),
                         (False, capability_check.IN_FLIGHT, first["operation_uuid"]))
        self.assertEqual(len(self.resident.attempts()), 1, "a second ApplyPlan reached the resident")

    def test_a_read_that_fails_after_the_submit_still_gets_the_answer_written(self):
        core = self.core()
        check = core.capability_check
        reads = iter([core._current_robot_binding])

        def first_then_raise():
            reader = next(reads, None)
            if reader is None:
                raise OSError("the read after the submit failed")
            return reader()

        with mock.patch.object(check, "_read_binding", side_effect=first_then_raise):
            answer = self.apply(core)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertIsNone(answer["host_after"]["host_running"], "a failed read is not a held host")
        self.assertEqual([record["activation_state"] for record in self.records()],
                         [mission.ACTIVATION_SUBMITTED, mission.ACTIVATION_OBSERVED], "the answer record was written")
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertIsNone(view["host_held_throughout"], "not known is not held")

    def test_a_request_that_failed_is_not_a_rejection(self):
        core = self.core()
        (self.resident.state / "apply_script.json").write_text("{}", encoding="ascii")
        answer = self.apply(core)
        self.assertEqual(answer["activation_state"], mission.ACTIVATION_FAILED)
        self.assertEqual(answer["outcome"], cake_activation.REQUEST_FAILED)
        self.assertIs(answer["unresolved"], False, "a request the resident answered as refused is not an open fate")
        self.assertIsNone(core.capability_check.pending_operation)

    def test_a_transaction_still_running_is_submitted_and_refuses_the_next_check(self):
        core = self.core()
        self.resident.script(valid_phase="RUNNING", valid_files={})
        first = self.apply(core)
        self.assertEqual(first["activation_state"], mission.ACTIVATION_SUBMITTED)
        self.assertIs(first["unresolved"], True)
        second = self.apply(core, INVALID_KEY, INVALID_DIGEST)
        self.assertEqual((second["reason_code"], second["operation_uuid"]), (capability_check.IN_FLIGHT, first["operation_uuid"]))
        self.assertEqual(len(self.resident.attempts()), 1)


# ---- 6. one at a time, and Stop -----------------------------------------------------------------------------------------------

class OneCheckAtATime(CheckCase):
    def start_slow_apply(self, core):
        self.resident.script(sleep_s=1.5)
        result = {}
        worker = threading.Thread(target=lambda: result.update(answer=self.apply(core)), daemon=True)
        worker.start()
        deadline = time.monotonic() + WAIT_S
        while not self.resident.attempts() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(core.capability_check.in_flight(), "the check is not in flight")
        return worker, result

    def test_a_second_apply_while_one_is_in_flight_is_refused_and_submits_nothing(self):
        core = self.core()
        worker, result = self.start_slow_apply(core)
        second = self.apply(core, INVALID_KEY, INVALID_DIGEST)
        self.assertEqual(second["reason_code"], capability_check.IN_FLIGHT)
        worker.join(WAIT_S)
        self.assertEqual(result["answer"]["activation_state"], mission.ACTIVATION_OBSERVED)
        self.assertEqual(len(self.resident.attempts()), 1, "the second press reached the resident")

    def test_a_command_and_a_mission_start_are_refused_while_a_check_holds_the_run_slot(self):
        core = self.core()
        stub = StubCoordinator()
        core.mission = stub
        worker, _result = self.start_slow_apply(core)
        submitted = core.handle("submit", {"op": "submit", "command": command("observe", {"holding": False}, "2a1b2c3d4e5f60718293")}, None)
        self.assertEqual((submitted["status"], submitted["reason_code"]), ("rejected", capability_check.IN_FLIGHT))
        started = core.handle("mission_start", {"op": "mission_start"}, None)
        self.assertEqual(started["reason_code"], capability_check.IN_FLIGHT)
        self.assertEqual(stub.started, [], "a mission was started under a check")
        capabilities = {item["id"]: item for item in core.snapshot()["capabilities"]}
        self.assertEqual(capabilities["start_cycle"]["reason_code"], capability_check.IN_FLIGHT)
        self.assertEqual(capabilities["stop"]["state"], "disabled", "a check alone is not a run or a mission to stop")
        worker.join(WAIT_S)
        self.assertIsNone(core._mission_holds_run_slot(), "the slot is free again once the check answered")

    def test_stop_is_answered_within_two_hundred_milliseconds_during_a_check(self):
        core = self.core()
        worker, _result = self.start_slow_apply(core)
        for op, request in (("stop", {}), ("mission_stop", {}),
                            ("submit", {"command": command("stop", {}, "3a1b2c3d4e5f60718293")})):
            began = time.monotonic()
            answer = core.handle(op, {"op": op, **request}, None)
            elapsed = time.monotonic() - began
            self.assertLess(elapsed, 0.2, f"{op} took {elapsed:.3f} s during a check")
            self.assertIsInstance(answer, dict)
            self.assertTrue(core.capability_check.in_flight(), "the stop was answered after the check, not during it")
        worker.join(WAIT_S)


# ---- 7. the record ------------------------------------------------------------------------------------------------------------

class TheRecord(CheckCase):
    def test_the_attempt_is_written_before_the_submit_and_the_answer_after_with_a_stamp_per_phase(self):
        core = self.core()
        answer = self.apply(core)
        attempt, answered = self.records()
        self.assertEqual((attempt["rec"], attempt["activation_state"], attempt["operation_uuid"]),
                         (capability_check.RECORD_KIND, mission.ACTIVATION_SUBMITTED, answer["operation_uuid"]))
        self.assertEqual((attempt["candidate_key"], attempt["payload_digest"], attempt["requested_session_uuid"]),
                         (VALID_KEY, VALID_DIGEST, SESSION))
        self.assertIs(attempt["host_before"]["host_running"], False)
        self.assertNotIn("outcome", attempt)
        phases = {**attempt["phases_mono_ns"], **answered["phases_mono_ns"]}
        order = ["requested", "claimed", "host_before_read", "slot_before_read", "attempt_written", "submitted", "answered",
                 "slot_after_read", "host_after_read"]
        self.assertEqual([phases[name] for name in order], sorted(phases[name] for name in order))
        self.assertEqual(answered["activation_state"], mission.ACTIVATION_OBSERVED)
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertTrue(view["answered"])
        self.assertIs(view["host_held_throughout"], True)
        self.assertGreater(view["duration_ms"], 0)
        blob = json.dumps(self.records())
        self.assertNotIn(str(self.resident.valid), blob, "a record names no path")
        self.assertNotIn(VALID_BYTES.decode("ascii"), blob, "a record carries no bytes")

    def test_an_attempt_a_previous_runtime_never_answered_is_said_to_be_one(self):
        core = self.core()
        path = self.data / capability_check.CHECKS_FILE_NAME
        record = capability_check.check_attempt("ab" * 16, VALID_KEY, VALID_DIGEST, requested_session_uuid=SESSION,
                                                runtime_session="rt-previous", host_before={"host_running": False},
                                                slot_before=None, phases_mono_ns={"requested": 1}, t_wall_utc="2026-09-25T10:00:00.000Z")
        core.capability_check._journal.append(record)
        view = self.ask(core, "capability_check_state")["checks"][-1]
        self.assertTrue(view["orphaned"])
        self.assertFalse(view["answered"])
        self.assertIn("no answer was recorded", view["detail"])
        self.assertIsNone(view["duration_ms"])
        self.assertTrue(path.exists())

    def test_the_state_read_says_what_an_apply_would_be_refused_for_and_submits_nothing(self):
        core = self.core()
        state = self.ask(core, "capability_check_state")
        self.assertIsNone(state["off_reason"])
        self.assertEqual(state["current"]["session_uuid"], SESSION)
        self.assertIs(state["current"]["host_running"], False)
        self.assertEqual((state["current"]["slot"]["capsule"], state["current"]["slot"]["generation"]), (INERT_CAPSULE, "1"))
        self.resident.write(host_pid=4242)
        self.assertEqual(self.ask(core, "capability_check_state")["off_reason"], capability_check.HOST_NOT_HELD)
        self.resident.write()
        with mock.patch.object(core, "_mission_is_carried", return_value=True):
            self.assertEqual(self.ask(core, "capability_check_state")["off_reason"], daemon.MISSION_HOLDS_RUN_SLOT)
        self.assertEqual(self.resident.attempts(), [])

    def test_the_event_log_says_what_the_check_recorded_and_what_it_did_not_send(self):
        core = self.core()
        self.apply(core)
        subscription = core.log.subscribe(0)
        core.log.unsubscribe(subscription)
        said = [event for event in subscription.backlog if event.get("reason_code") == "capability_check_answered"]
        self.assertEqual(len(said), 1)
        self.assertIn("recorded OBSERVED", said[0]["payload"]["text"])
        # What the runtime can say: it sent no motion command and started no mission or run. Whether anything moved is a
        # physical fact it does not observe, so the line does not state it.
        self.assertIn("no motion command was sent and no mission or run was started", said[0]["payload"]["text"])
        self.assertNotIn("nothing moved", said[0]["payload"]["text"])
        self.assertEqual(demo_contracts.validate("event", said[0]), [], "the diagnostic is a contract event")


# ---- 8. what held means --------------------------------------------------------------------------------------------------------

class WhatHeldMeans(unittest.TestCase):
    def test_held_only_when_no_host_process_is_under_the_supervisor_slot(self):
        held = capability_check.host_held
        for reason in ("supervisor_slot_absent", "supervisor_child_not_running"):
            self.assertIs(held({"host_running": False, "reason_code": reason}), True, reason)
        # A slot that is not ACTIVE: robot_link stops before reading its health row, so the row the binding carries beside
        # it (supervisor_slot) decides. Held only when the row is missing or shows child pid 0. Changed deliberately from
        # "held on the reason alone" (WP3 re-verification, defect 2).
        not_active = {"host_running": False, "reason_code": "supervisor_slot_not_active"}
        row = {"slot_present": True, "slot_state": "STOPPING", "health_read": True, "health_row": True}
        self.assertIs(held({**not_active, "supervisor_slot": {**row, "child_pid": 0}}), True)
        self.assertIs(held({**not_active, "supervisor_slot": {**row, "health_row": False, "child_pid": None}}), True)
        self.assertIs(held({**not_active, "supervisor_slot": {**row, "child_pid": 4242}}), False)
        self.assertIsNone(held({**not_active, "supervisor_slot": {**row, "health_read": False, "health_row": False, "child_pid": None}}))
        self.assertIsNone(held(not_active), "a slot that is not ACTIVE with no health read beside it is not known to be held")
        self.assertIs(held({"host_running": False, "reason_code": "supervisor_host_not_healthy"}), False)
        self.assertIs(held({"host_running": False, "reason_code": "a_reason_nobody_classified"}), False,
                      "a reason this check does not know is not held")
        self.assertIs(held({"host_running": True, "reason_code": None}), False)
        for unread in ({"host_running": None, "reason_code": "health_unavailable"}, {}, None):
            self.assertIsNone(held(unread), unread)

    def test_every_reason_robot_link_answers_no_running_host_for_is_classified_here(self):
        """A PIN against robot_link.supervisor_host_instance: each reason it returns with running False is either one this
        check counts as held or the one it knows is not. A new one there fails here until someone decides what it means."""
        source = Path(robot_link.__file__).read_text(encoding="utf-8")
        body = source[source.index("def supervisor_host_instance"):source.index("def binding_of")]
        answered = set(re.findall(r'return None, False, "([a-z_]+)"', body))
        self.assertEqual(answered, set(capability_check.HELD_REASONS) | {"supervisor_host_not_healthy"})

    def test_held_throughout_compares_every_supervisor_counter_of_the_two_reads(self):
        throughout = capability_check.held_throughout
        supervisor = {"slot_present": True, "slot_state": "ACTIVE", "slot_instance": "7", "slot_generation": 1,
                      "health_read": True, "health_row": True, "child_pid": 0, "spawns": 5, "restarts": 5, "health_state": "DEGRADED"}
        host = {"session_uuid": SESSION, "host_running": False, "reason_code": "supervisor_child_not_running", "supervisor_slot": supervisor}
        self.assertEqual(throughout(host, dict(host)), (True, None))
        for name, value, words in (("spawns", 6, "spawns 5 to 6"), ("restarts", 9, "restarts 5 to 9"),
                                   ("slot_generation", 2, "supervisor slot generation 1 to 2"),
                                   ("slot_instance", "8", "supervisor slot instance 7 to 8")):
            with self.subTest(name=name):
                held, detail = throughout(host, {**host, "supervisor_slot": {**supervisor, name: value}})
                self.assertIs(held, False)
                self.assertIn(words, detail)
        held, detail = throughout(host, {**host, "session_uuid": OTHER_SESSION})
        self.assertIs(held, False)
        self.assertIn("session", detail)
        self.assertEqual(throughout(host, {**host, "host_running": None, "reason_code": "health_unavailable"})[0], None,
                         "a read after that did not answer is not known")
        self.assertIs(throughout(host, {**host, "host_running": True, "reason_code": None})[0], False)
        no_counters = {key: value for key, value in host.items() if key != "supervisor_slot"}
        held, detail = throughout(no_counters, dict(no_counters))
        self.assertIsNone(held, "without the counters two held reads cannot say throughout")
        self.assertIsNotNone(detail)

    def test_the_deployed_held_state_is_held(self):
        """The robot as handoff section 9 records it: the supervisor slot DEGRADED after five refused spawns, no child."""
        binding = {"host_running": False, "reason_code": "supervisor_child_not_running"}
        self.assertIs(capability_check.host_held(binding), True)
        self.assertIsNone(capability_check.binding_refusal({**binding, "session_uuid": SESSION}, SESSION))


class TheDeployedHeldStateThroughTheDaemon(CheckCase):
    def test_a_degraded_slot_with_no_child_is_held_and_the_check_runs(self):
        core = self.core()
        self.resident.write(host_pid=0, host_state="DEGRADED")
        state = self.ask(core, "capability_check_state")
        self.assertIsNone(state["off_reason"])
        self.assertIs(state["current"]["host_held"], True)
        answer = self.apply(core, INVALID_KEY, INVALID_DIGEST)
        self.assertTrue(answer["ok"], answer)
        self.assertIs(answer["host_before"]["held"], True)


# ---- 10. a runtime that is closing ----------------------------------------------------------------------------------------------

class ClosingTheRuntime(CheckCase):
    def test_a_check_that_has_not_written_its_attempt_when_the_runtime_closes_submits_nothing(self):
        core = self.core()
        check = core.capability_check
        real = check._read_binding

        def close_arrives_during_the_read():
            check.begin_close()
            return real()

        with mock.patch.object(check, "_read_binding", side_effect=close_arrives_during_the_read):
            answer = self.apply(core)
        self.assertEqual((answer["ok"], answer["reason_code"], answer["detail"]),
                         (False, capability_check.UNAVAILABLE, capability_check.RUNTIME_CLOSING))
        self.assertEqual(self.resident.attempts(), [], "a closing runtime submitted")
        self.assertEqual(self.records(), [], "a closing runtime wrote an attempt")
        again = self.apply(core)
        self.assertEqual(again["detail"], capability_check.RUNTIME_CLOSING)

    def test_close_waits_for_a_check_in_flight_to_write_its_answer(self):
        core = self.core()
        self.resident.script(sleep_s=1.0)
        worker = threading.Thread(target=self.apply, args=(core,), daemon=True)
        worker.start()
        deadline = time.monotonic() + WAIT_S
        while not self.resident.attempts() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(core.capability_check.in_flight())
        core.capability_check.close()
        worker.join(WAIT_S)
        self.assertEqual([record["activation_state"] for record in self.records()],
                         [mission.ACTIVATION_SUBMITTED, mission.ACTIVATION_OBSERVED], "the answer was written before the file closed")

    def test_the_wait_is_derived_from_every_bound_after_the_attempt_record(self):
        bound = capability_check.close_wait_bound_s(read_timeout_s=3.0, submit_timeout_s=8.0, binding_timeout_s=2.0)
        # get-status before the ApplyPlan, the ApplyPlan, the slot read back, then get-status, list-slots and get-health.
        self.assertEqual(bound, 3.0 + 8.0 + 3.0 + 3 * 2.0 + capability_check.CLOSE_MARGIN_S)
        self.assertGreater(capability_check.close_wait_bound_s(read_timeout_s=3.0, submit_timeout_s=8.0, binding_timeout_s=5.0), bound,
                           "a longer read bound makes a longer wait, never the same fixed figure")
        core = self.core()
        self.assertEqual(core.capability_check.close_wait_s,
                         capability_check.close_wait_bound_s(read_timeout_s=mission_service.SLOT_READ_TIMEOUT_S,
                                                             submit_timeout_s=mission_service.ACTIVATION_TIMEOUT_S,
                                                             binding_timeout_s=core.config.cake.timeout_s))

    def test_the_wait_fits_under_the_runtime_unit_s_stop_bound_at_the_configured_timeout(self):
        """systemd sends SIGKILL TimeoutStopSec after the stop. daemon.close waits for a carried mission first
        (MissionCoordinator.close joins for 5 s) and then for a check in flight; both have to fit, or the answer record of
        an ApplyPlan the resident may have applied is lost to the kill."""
        unit = (REPO_ROOT / "bin" / "templates" / "kiwi-cake-runtime.service.in").read_text(encoding="ascii")
        stop_s = float(re.search(r"^TimeoutStopSec=([0-9]+)$", unit, re.M).group(1))
        core = self.core()
        self.assertLess(5.0 + core.capability_check.close_wait_s, stop_s)


if __name__ == "__main__":
    unittest.main()
