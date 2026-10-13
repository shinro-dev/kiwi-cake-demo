# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""Shared test scaffolding for apps/console-api.

THE STUB RUNTIME has exactly RuntimeClient's surface and nothing behind it. It lets a test decide what the runtime answers,
including the answers a real runtime gives only rarely: a lost submit, a resync gap, a contract mismatch. The tests that
must see a REAL command path use the runtime daemon in --synthetic mode instead, whose only child is the repository's
fake controller.

FORBIDDEN STRINGS ARE BUILT AT RUN TIME. A private address, a user name, a home path and a device id are what the leak
tests look for in a response body, so they must not exist as literals in a committed file. Each one is assembled from
pieces here, which is also why no test may print them.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parents[1]
for _entry in (str(APP_DIR), str(REPO_ROOT / "packages" / "demo-runtime"), str(REPO_ROOT / "packages" / "demo-contracts" / "python")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from console_api.app import Options, build_app  # noqa: E402
from console_api.security import CONSOLE_HEADER, CONSOLE_HEADER_VALUE, CREDENTIAL_HEADER  # noqa: E402
from demo_runtime.journal import payload_digest  # noqa: E402
from demo_runtime.rpc import ReadOnlyClient, ResponseLost, RuntimeUnavailable  # noqa: E402

import demo_contracts  # noqa: E402

CONTRACT_VERSION = demo_contracts.CONTRACT_VERSION
CREDENTIAL = "test-" + "credential-" + "0123456789abcdef"

# Strings the repository's text gates refuse, assembled so that no literal exists in this file.
PRIVATE_ADDRESS = ".".join(("192", "168", "7", "41"))
USER_NAME = "operator" + "name"
HOME_PATH = "/" + "home" + "/" + USER_NAME + "/" + "private-tree"
SSH_TARGET = USER_NAME + "@" + PRIVATE_ADDRESS
CAMERA_ID = "usb-" + "Vendor_Cam_" + "SN" + "7654321" + "-video-index0"
LEAK_STRINGS = (PRIVATE_ADDRESS, USER_NAME, HOME_PATH, SSH_TARGET, CAMERA_ID)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ---- contract objects a stub can answer with --------------------------------------------------------------------------------

def cake_status_object(*, reachable: bool = False, stale: bool = False, slots: int = 0, session: str = "01a0cdba8c9c7b09b03c1f945f489104") -> dict:
    """A `cake_status` answer, built through the runtime's own projection so a test cannot pin a shape it does not produce."""
    from demo_runtime import read_models

    probe = {"cake_configured": reachable or stale, "cake_retained": None, "cake_session_changed": None}
    if reachable or stale:
        reading = {"reachable": True, "reason_code": None,
                   "identity": {"session_uuid": session, "build_identity": "d3" * 32, "config_identity": "8b" * 32,
                                "target_profile_digest": "84" * 32, "plan_digest": "c9" * 32},
                   "counts": {"epoch": 0, "slots": slots, "bindings": 0, "resources": 0, "replay": 0, "snapshot_present": 1},
                   "transaction_state": "none", "flight": {"records": 3, "oldest": 0, "next": 2},
                   "mission_participation": False, "observed_wall_utc": "2026-01-01T00:00:00.000Z",
                   "observed_mono_ns": 1_000_000_000, "clock_domain": "laptop_monotonic"}
        if stale:  # contact lost: the last real reading is retained and the current read failed
            probe["cake_retained"] = reading
            probe["inventory"] = {"reachable": False, "reason_code": "admin_socket_absent", "identity": {}, "counts": {}, "flight": None,
                                  "mission_participation": False, "observed_wall_utc": "2026-01-01T00:00:30.000Z",
                                  "observed_mono_ns": 31_000_000_000, "clock_domain": "laptop_monotonic"}
        else:
            probe["inventory"] = reading
    return read_models.cake_status_view(probe, now_mono_ns=2_000_000_000)


ROBOT_SESSION = "01a0cdba8c9c7b09b03c1f945f489104"
ROBOT_HOST_PID = 4242
ROBOT_BUILD = "d3" * 32
# The supervisor host instance a confirmation names. The slot instance is a u64 larger than 2**53, as on the real robot.
ROBOT_HOST_INSTANCE = {"slot_instance": "6003298303284871169", "spawn_sequence": 2, "pid": ROBOT_HOST_PID}


def robot_status_object(*, reachable: bool = True) -> dict:
    """A `robot_status` answer, built through demo_runtime.robot_link's own projection with no robot anywhere: a config
    that names a robot resident, a reading shaped like the one read_robot returns, and no forward or readiness."""
    from demo_runtime import cake_admin, robot_link
    from demo_runtime.config import RuntimeConfig

    config = RuntimeConfig.from_mapping({
        "data_root": "/tmp/kc-support-data", "saved_pose_file": "pose.json", "pad_template_dir": "pad", "observation_root": "obs",
        "host": {"robot_id": "unit-" + "0042"}, "pi_ssh_target": "operator@robot.example",
        "cake": {"admin_probe_path": "/opt/cake/bin/admin-probe",
                 "robot": {"remote_admin_socket_path": "/run/user/1000/kc-lekiwi.sock", "local_admin_socket_path": "/run/kc-test/robot-admin.sock",
                           "expected_build_identity": ROBOT_BUILD, "display_label": "bench LeKiwi"}}})
    inventory = cake_admin.unavailable("robot_unreachable")
    if reachable:
        supervisor_slot = "50505430405080500000737570657276"
        inventory = {"reachable": True, "reason_code": None, "counts": {"epoch": 3, "slots": 2},
                     "identity": {"session_uuid": ROBOT_SESSION, "build_identity": ROBOT_BUILD,
                                  "target_profile_digest": "841c3e3f73e01e48ece200ce11385aa2dc4945df51de6bef6d1f8e8bd97d15b3"},
                     "transaction_state": "none", "flight": None, "mission_participation": False,
                     **cake_admin.observed_now(),
                     "robot_detail": {"slots_reason": None, "slots_text": "",
                                      "slots": [{"name": "supervisor.lekiwi", "uuid": supervisor_slot, "capsule": "a1" * 32,
                                                 "generation": 1, "instance": ROBOT_HOST_INSTANCE["slot_instance"], "state": "ACTIVE"}],
                                      "health_reason": None,
                                      "health": [{"slot": supervisor_slot, "state_name": "HEALTHY", "child_pid": ROBOT_HOST_PID,
                                                  "restarts": 1, "spawns": ROBOT_HOST_INSTANCE["spawn_sequence"], "last_status": 0}],
                                      "updates_reason": None, "updates": robot_link.parse_updates([])}}
    return robot_link.robot_status_view(config=config, probe={"inventory": inventory}, retained=None, session_changed=None,
                                        permission=robot_link.MotionPermission("/nonexistent-kc-audit-dir"),
                                        forward_report={"state": "connected", "reason": None, "since_wall_utc": "2026-09-23T10:00:00.000Z",
                                                        "since_age_ms": 1.0, "starts": 1, "deaths": 0, "restarts": 0, "next_attempt_in_ms": None})


def snapshot_object(*, data_mode: str = "synthetic", run_active: bool = False, seq_cursor: int = 12, retained_from_seq: int = 1) -> dict:
    from demo_runtime import read_models
    from demo_runtime.journal import RUNTIME_VERSION

    stats = read_models.SessionStats()
    active = {"run_id": "sc-20260101T000000Z-abcd1234", "command_id": "abcd1234ef0123456789", "command_type": "start_cycle",
              "status": "running", "state": "SEARCH", "started_seq": 3} if run_active else None
    replay = data_mode == "replay"
    return read_models.build_snapshot(
        runtime_session="rt-20260101T000000Z-0badc0de", seq_cursor=seq_cursor, retained_from_seq=retained_from_seq, data_mode=data_mode,
        link={"state": "unknown", "source_stamp": None, "receive_age_ms": None},
        owner={"kind": "autonomous" if run_active else "free", "run_id": active["run_id"] if active else None, "lease": None},
        active_run=active, last_outcome=None,
        capability_list=read_models.capabilities(block_code="replay_only" if replay else ("run_active" if run_active else None),
                                                 run_active=run_active, replay_only=replay),
        component_list=read_models.components(stats=stats, run_active=run_active, last_exit=None, replay_only=replay, runtime_version=RUNTIME_VERSION),
        cake=read_models.cake_view({"artifacts": False, "resident": False, "admin_socket": False}), evidence=stats.evidence())


def mission_view_object(*, waiting: bool = True) -> dict:
    """A mission view, built through the runtime's OWN records and fold so a test cannot pin a shape it does not
    produce. It is not a contract object and carries no contract version: the mission is its own surface."""
    from demo_runtime import mission

    targets = [{"target_id": "cube_near", "label": "the reachable cube", "reachable": True, "requires_capability": False},
               {"target_id": "cube_far", "label": "the far cube", "reachable": False, "requires_capability": True}]
    records = [mission.mission_started("mission-0123456789ab", targets, t_wall_utc="2026-01-01T00:00:00.000Z", data_mode="synthetic", performs_motion=False, execution_label="recording-rehearsal"),
               mission.target_selected("mission-0123456789ab", "cube_near", t_wall_utc="2026-01-01T00:00:01.000Z"),
               mission.target_outcome("mission-0123456789ab", "cube_near", mission.TARGET_PLACED, run_id="sc-20260101T000000Z-abcd1234",
                                      t_wall_utc="2026-01-01T00:00:02.000Z"),
               mission.target_selected("mission-0123456789ab", "cube_far", t_wall_utc="2026-01-01T00:00:03.000Z")]
    if waiting:
        records.append(mission.target_waiting("mission-0123456789ab", "cube_far", mission.WAITING_ON_CAPABILITY,
                                              t_wall_utc="2026-01-01T00:00:04.000Z"))
    return mission.mission_view(mission.fold_missions(records)["mission-0123456789ab"])


def unresolved_object(*, command_id: str = "abcd1234ef0123456789", classification: str = "ran_and_was_interrupted") -> dict:
    """The shape demo_runtime.supervisor.unresolved_detail() returns, which the daemon hangs off the snapshot under the
    additive key 'unresolved_command'. Built here rather than imported so a test can state the exact answer it wants."""
    from demo_runtime.supervisor import COMMANDS_BLOCKED_WHILE_UNRESOLVED, FINDINGS

    evidence = {
        "command_id": command_id, "run_id": "sc-20260101T000000Z-abcd1234", "command_type": "start_cycle",
        "journal": {"status": "uncertain", "reason_code": "runtime_restarted_before_child_recorded", "child_identity": True,
                    "adopted": False, "accepted_wall_utc": "2026-01-01T00:00:00.000Z", "resolved": False},
        "process": {"alive": False, "readable": True, "found_by": None, "pid_checked": True},
        "run_files": {"readable": True, "terminal_record": False, "event_records": 41, "output_present": True, "outcome": None},
        "observations": {"checked": True, "readable": True, "files_after_accept": 3},
        "classification": classification, "unreadable": [], "checked_wall_utc": "2026-01-01T00:01:00.000Z",
    }
    return {"command_id": command_id, "run_id": evidence["run_id"], "command_type": "start_cycle",
            "reason_code": "runtime_restarted_before_child_recorded", "classification": classification,
            "block_reason_code": "owner_unresolved", "evidence": evidence,
            "question": "this command started a controller that left no terminal record, and no process of it is alive now; confirm that none is "
                        "running and say how it ended, after looking at the robot",
            "findings": list(FINDINGS), "attestations": [], "blocked_commands": list(COMMANDS_BLOCKED_WHILE_UNRESOLVED)}


def event_object(seq: int, *, kind: str = "diagnostic.text", payload=None, run_id=None) -> dict:
    return {"schema_version": CONTRACT_VERSION, "seq": seq, "run_id": run_id, "action_id": None, "observation_id": None, "kind": kind,
            "producer": {"name": "demo-runtime", "version": "0.1.0", "artifact_digest": None, "contract_version": CONTRACT_VERSION},
            "source_session": "rt-20260101T000000Z-0badc0de", "t_mono_ns": 1000 + seq, "clock_domain": "laptop_monotonic",
            "t_wall_utc": "2026-01-01T00:00:00.000Z", "reason_code": "test_event",
            "payload": payload or {"stream": "log", "level": "info", "text": f"line {seq}", "redacted": False}, "provenance": None}


def command_object(command_type: str = "observe", args=None, command_id: str = "abcd1234ef0123456789") -> dict:
    return {"contract_version": CONTRACT_VERSION, "command_id": command_id, "type": command_type,
            "args": args if args is not None else {"holding": False},
            "client": {"session_id": "tab-0123456789ab", "contract_version": CONTRACT_VERSION}}


# ---- the stub runtime -------------------------------------------------------------------------------------------------------

class StubRuntime:
    """RuntimeClient's surface, answered from memory. Every method may be steered by a test."""

    def __init__(self, *, data_mode: str = "synthetic", hello_ok: bool = True, retained_from_seq: int = 1):
        self.read_only = not hello_ok
        self.contract_version = CONTRACT_VERSION
        self.hello_ok = hello_ok
        self.data_mode = data_mode
        self.retained_from_seq = retained_from_seq
        self.unavailable = False
        self.break_stream = False
        self.lose_submit = False
        self.submits: list = []
        self.results: dict = {}
        self.observations: dict = {}
        self.frames: dict = {}
        self.history_runs: list = []
        self.run_events: dict = {}
        self.run_output_text: dict = {}
        self.stop_answer = {"ok": True, "stop": None}
        # What a SUBMITTED stop command comes back as, which is the journaled path: a contract command_result, exactly
        # as demo_runtime.supervisor._submit_stop builds one. A test that wants the other answer sets it to the
        # no_active_run shape. This is separate from stop_answer, which belongs to the runtime's plain stop op.
        self.stop_command_result = {"status": "finished", "reason_code": None, "run_id": "sc-20260101T000000Z-abcd1234"}
        # The mission's part of a submitted stop, which a runtime with a mission surface puts BESIDE the command_result's
        # fields (demo_runtime.daemon._op_submit). None: the runtime has no mission surface and sends no such key.
        self.stop_command_mission = None
        self.runtime_session = "rt-20260101T000000Z-0badc0de"
        # The snapshot's two optional properties: the command whose execution is not known, and what the runtime says
        # about the host lease. Both are None until a test sets them, which is the answer "there is neither".
        self.unresolved_command = None
        self.lease = None
        # The Cake resident status surface, which is versioned apart from the contract. The default is the answer a
        # runtime that was told about no resident gives, which is what every deployment gave before one was named.
        self.cake_status_answer = cake_status_object()
        # The robot's resident surface, versioned apart like cake_status, built through the runtime's own projection.
        self.robot_status_answer = robot_status_object()
        self.robot_confirm_answer = {"ok": True, "confirmed_session_uuid": ROBOT_SESSION, "confirmed_host_instance": dict(ROBOT_HOST_INSTANCE),
                                     "confirmed_wall_utc": "2026-09-23T10:00:00.000Z", "motion_permitted": True, "reason_code": None}
        self.robot_confirmations: list = []
        # The capability slot check, versioned apart like the robot surface. What the stub was ASKED is recorded, because
        # the property these routes have to prove is that this process forwards a key, a digest and a session and nothing
        # else, and nothing at all when the guard refuses.
        self.capability_check_state_answer = {
            "ok": True, "capability_check_version": "1", "available": True, "in_flight": False, "pending_operation_uuid": None,
            "off_reason": None, "candidates": [{"key": "mobile-demo-swapped", "payload_digest": "sha256:" + "ab" * 32}],
            "current": {"session_uuid": ROBOT_SESSION, "build_identity": "d3" * 32, "host_running": False, "host_held": True,
                        "host_reason_code": "supervisor_child_not_running", "slot": None,
                        "observed_wall_utc": "2026-09-25T10:00:00.000Z", "age_ms": 12.0},
            "checks": [], "checks_dropped": 0, "damaged_lines": 0}
        self.capability_check_apply_answer = {"ok": True, "operation_uuid": "0e" * 16, "candidate_key": "mobile-demo-swapped",
                                              "payload_digest": "sha256:" + "ab" * 32, "activation_state": "OBSERVED",
                                              "outcome": "ACCEPTED", "transaction_phase": "COMPLETED",
                                              "detail": "the transaction completed", "resident_session_changed": False}
        self.capability_check_applies: list = []
        # The signature policy check, versioned apart like the slot check. The read is answered whole; a run is COUNTED,
        # because the property its route has to prove is that this process forwards nothing at all with it.
        self.signature_state_answer = {"ok": True, "signature_check_version": "1", "available": True, "in_flight": False,
                                       "off_reason": None, "policy": "require", "checks": [], "checks_dropped": 0, "damaged_lines": 0}
        self.signature_check_answer = {
            "ok": True, "check_uuid": "0c" * 16, "runtime_session": "rt-20260101T000000Z-0badc0de", "t_wall_utc": "2026-09-26T10:00:00.000Z",
            "policy": "require", "trusted_keys": ["82" * 32], "throwaway_public_key": "91" * 32, "capsules": 1, "unsigned_twins": 0,
            "rows": [{"capsule": "18" * 32, "kind": "signed_capsule", "key": "deployment", "expected": "admitted", "expected_reason": None,
                      "observed": "admitted", "reason_code": None, "admitted": True, "as_expected": True},
                     {"capsule": "18" * 32, "kind": "signed_capsule", "key": "throwaway", "expected": "refused",
                      "expected_reason": "signature_key_id_not_trusted", "observed": "refused", "reason_code": "signature_key_id_not_trusted",
                      "admitted": False, "as_expected": True},
                     {"capsule": None, "kind": "unsigned_twin", "key": "deployment", "expected": "refused", "expected_reason": "signature_missing",
                      "observed": "skipped", "reason_code": "unsigned_twin_not_staged", "admitted": None, "as_expected": None}],
            "rows_as_expected": 2, "rows_not_as_expected": 0, "rows_skipped": 1, "rows_error": 0, "as_expected": True, "complete": False,
            "duration_ms": 812.5}
        self.signature_runs = 0
        # The mission surface, versioned apart from the contract like cake_status. Every answer is what a test sets,
        # and what the stub was ASKED is recorded, because the property most of these routes have to prove is that
        # this process forwards a candidate key and nothing else.
        self.mission_state_answer = mission_view_object()
        self.mission_candidates_answer = {"ok": True, "candidates": [{"key": "mobile_approach.activate", "payload_digest": "sha256:" + "ab" * 32}]}
        self.mission_start_answer = {"ok": True, "mission_id": "mission-0123456789ab"}
        self.mission_stop_answer = {"ok": True, "mission_id": "mission-0123456789ab", "stopping": True}
        self.mission_activate_answer = {"ok": True, "mission_id": "mission-0123456789ab", "operation_uuid": "0f" * 16,
                                        "activation_state": "OBSERVED", "outcome": "ACCEPTED",
                                        "transaction_phase": "COMPLETED", "detail": "the transaction completed",
                                        "stopped_during_activation": False}
        self.mission_retry_answer = {"ok": True, "mission_id": "mission-0123456789ab", "target_id": "cube", "attempt": 1, "retrying": True}
        self.mission_starts: list = []
        self.mission_stops = 0
        self.mission_activations: list = []
        self.mission_retries: list = []
        self.resolve_answer = None
        self.resolves: list = []
        self._log: list = []
        self._telemetry: list = []
        self._condition = threading.Condition()

    # -- what a test drives
    def emit(self, event: dict) -> None:
        with self._condition:
            self._log.append(event)
            self._condition.notify_all()

    def publish(self, record: dict) -> None:
        with self._condition:
            self._telemetry.append(record)
            self._condition.notify_all()

    # -- the client surface
    def _guard(self):
        if self.unavailable:
            raise RuntimeUnavailable("the stub runtime is unreachable")

    def hello(self) -> dict:
        self._guard()
        if not self.hello_ok:
            self.read_only = True
            return {"ok": False, "reason_code": "contract_incompatible", "contract_version": CONTRACT_VERSION}
        self.read_only = False
        return {"ok": True, "contract_version": CONTRACT_VERSION, "runtime_session": self.runtime_session}

    def snapshot(self) -> dict:
        self._guard()
        snapshot = snapshot_object(data_mode=self.data_mode, retained_from_seq=self.retained_from_seq)
        snapshot["runtime_session"] = self.runtime_session
        if self.unresolved_command is not None:
            snapshot["unresolved_command"] = self.unresolved_command  # a contract property now, exactly as daemon._op_snapshot sets it
        if self.lease is not None:
            snapshot["lease"] = self.lease
        return snapshot

    def status(self) -> dict:
        self._guard()
        return {"ok": True, "mode": self.data_mode}

    def cake_status(self) -> dict:
        self._guard()
        return self.cake_status_answer

    def robot_status(self) -> dict:
        self._guard()
        return self.robot_status_answer

    def robot_confirm_session(self, session_uuid: str, host_instance: dict) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.robot_confirmations.append((session_uuid, host_instance))
        return self.robot_confirm_answer

    def capability_check_state(self) -> dict:
        self._guard()
        return self.capability_check_state_answer

    def capability_check_apply(self, candidate_key: str, payload_digest: str, session_uuid: str) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.capability_check_applies.append((candidate_key, payload_digest, session_uuid))
        return self.capability_check_apply_answer

    def capability_signature_state(self) -> dict:
        self._guard()
        return self.signature_state_answer

    def capability_signature_check(self) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.signature_runs += 1
        return self.signature_check_answer

    def result(self, command_id: str) -> dict:
        self._guard()
        return self.results.get(command_id) or {"ok": False, "reason_code": "unknown_command"}

    def history(self) -> dict:
        self._guard()
        return {"ok": True, "runs": list(self.history_runs)}

    def history_events(self, run_id: str) -> dict:
        self._guard()
        if run_id not in self.run_events:
            return {"ok": False, "reason_code": "unknown_run"}
        return {"ok": True, "run_id": run_id, "events": self.run_events[run_id], "origins": []}

    def run_output(self, run_id: str, stream: str = "stdout", max_bytes=None) -> dict:
        self._guard()
        if run_id not in self.run_output_text:
            return {"ok": False, "reason_code": "unknown_run"}
        text = self.run_output_text[run_id].get(stream, "")
        return {"ok": True, "run_id": run_id, "stream": stream, "text": text,
                "bytes": len(text.encode("utf-8")), "total_bytes": len(text.encode("utf-8")), "truncated": False}

    def observation(self, observation_id: str, what: str = "record") -> dict:
        self._guard()
        data = self.observations.get((observation_id, what))
        if data is None:
            return {"ok": False, "reason_code": "not_found"}
        return {"ok": True, "observation_id": observation_id, "what": what, "data": data}

    def frame(self, observation_id: str, what: str = "raw", max_width: int = 640) -> dict:
        self._guard()
        rendition = self.frames.get((observation_id, what))
        return rendition if rendition is not None else {"ok": False, "reason_code": "not_found"}

    def submit(self, command: dict) -> dict:
        """The runtime's own idempotency, in miniature: a recorded result is returned and nothing is started twice,
        and the receipt carries the digest of the command that was RECORDED under that id, which is what lets a caller
        see that a repeat is not the same request."""
        self._guard()
        self.submits.append(command)
        command_id = command.get("command_id", "")
        if self.lose_submit:
            raise ResponseLost(command_id)
        if self.data_mode == "replay":
            return {"ok": False, "reason_code": "replay_only"}
        known = self.results.get(command_id)
        if known is not None:
            return known
        recorded = {"status": "accepted", "reason_code": None, "run_id": "sc-20260101T000000Z-abcd1234"}
        if command.get("type") == "stop":
            recorded = dict(self.stop_command_result)
        answer = {"command_id": command_id, **recorded, "payload_digest": payload_digest(command)}
        self.results[command_id] = answer
        if command.get("type") == "stop" and self.stop_command_mission is not None:
            return {**answer, "mission": dict(self.stop_command_mission)}
        return answer

    def stop(self) -> dict:
        self._guard()
        return self.stop_answer

    # -- the mission, which is its own surface and carries no contract object
    def mission_state(self) -> dict:
        self._guard()
        return self.mission_state_answer

    def mission_candidates(self) -> dict:
        self._guard()
        return self.mission_candidates_answer

    def mission_start(self, targets=None) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.mission_starts.append(targets)
        return self.mission_start_answer

    def mission_stop(self) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.mission_stops += 1
        return self.mission_stop_answer

    def mission_activate(self, candidate_key: str) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.mission_activations.append(candidate_key)
        return self.mission_activate_answer

    def mission_retry(self, mission_id=None) -> dict:
        self._guard()
        if self.read_only:
            raise ReadOnlyClient("the runtime speaks another contract version")
        self.mission_retries.append(mission_id)
        return self.mission_retry_answer

    def resolve(self, command_id: str, finding: str, note=None) -> dict:
        """RuntimeClient.resolve's surface. The stub records what it was asked and answers what a test set; it never
        resubmits anything, which is the property the API test is about."""
        self._guard()
        if self.data_mode == "replay":
            return {"ok": False, "reason_code": "replay_only"}
        self.resolves.append({"command_id": command_id, "finding": finding, "note": note})
        if self.resolve_answer is not None:
            return self.resolve_answer
        return {"ok": True, "command_id": command_id, "owner": "free", "cleared": True, "cleared_by": "operator_attestation",
                "classification": "ran_and_was_interrupted", "evidence": unresolved_object(command_id=command_id)["evidence"],
                "attestation": {"finding": finding, "note": note, "classification": "ran_and_was_interrupted", "clears_block": True,
                                "refused": None, "t_wall_utc": "2026-01-01T00:01:00.000Z"}}

    def events(self, since: int = 0, *, idle_timeout_s=None):
        """Blocking like the real client: the gap first when there is one, then every event, then a wait for more."""
        self._guard()
        if since + 1 < self.retained_from_seq:
            yield {"resync": {"retained_from_seq": self.retained_from_seq, "missed": [since + 1, self.retained_from_seq - 1]}}
            since = self.retained_from_seq - 1
        deadline = None if idle_timeout_s is None else time.monotonic() + idle_timeout_s
        while True:
            if self.break_stream:
                raise OSError("the stub broke this stream")
            with self._condition:
                pending = [event for event in self._log if event["seq"] > since]
                if not pending:
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    self._condition.wait(timeout=0.02)
                    continue
            for event in pending:
                since = event["seq"]
                yield event
            if idle_timeout_s is not None:
                deadline = time.monotonic() + idle_timeout_s

    def telemetry(self, *, idle_timeout_s=None):
        self._guard()
        index, deadline = 0, None if idle_timeout_s is None else time.monotonic() + idle_timeout_s
        while True:
            with self._condition:
                pending = self._telemetry[index:]
                if not pending:
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    self._condition.wait(timeout=0.02)
                    continue
            index += len(pending)
            for record in pending:
                yield record
            if idle_timeout_s is not None:
                deadline = time.monotonic() + idle_timeout_s


# ---- the fixture-directory hook: recorded runs served as a finished-run list ---------------------------------------------------

# The one place the local replay stage names its directory. A test never reads it; the stage script does.
FIXTURE_DIRECTORY_ENV = "KC_CONSOLE_FIXTURE_DIR"
# The console's own ports on the operator's machine. The replay stage never binds either of them.
RESERVED_CONSOLE_PORTS = (8770, 8771)


def _history_entry(run_id: str, events: list) -> dict:
    """A finished-run row, derived from the run's own records the way the runtime derives one: nothing guessed."""
    started = next((event for event in events if event.get("kind") == "run.started"), None)
    finished = next((event for event in reversed(events) if event.get("kind") == "run.finished"), None)
    begun = (started or {}).get("payload") or {}
    ended = (finished or {}).get("payload") or {}
    producer = ((started or finished or {}).get("producer") or {})
    return {"run_id": run_id, "command_id": begun.get("command_id"), "command_type": begun.get("command_type"),
            "started_wall_utc": (started or {}).get("t_wall_utc"), "finished_wall_utc": (finished or {}).get("t_wall_utc"),
            "outcome": ended.get("outcome"), "exit_status": ended.get("exit_status"),
            "evidence_complete": ended.get("evidence_complete"), "evidence_gaps": ended.get("evidence_gaps"),
            "artifact_digest": producer.get("artifact_digest"), "adopted": False, "data_mode": begun.get("data_mode"),
            "bundle_digest": None}


def load_fixture_directory(stub: "StubRuntime", directory) -> list:
    """Serve a directory of recorded runs through a stub runtime: every `<run>.events.json` (the runtime's history-events
    answer, or a bare list of events) becomes a finished run with its events, and a `<run>.output.json` beside it (the
    runtime's run-output answer) becomes that run's stdout. Anything else in the directory is ignored. Returns the run
    ids loaded, newest first, in the order the finished-run list shows them. The directory is read, never written, and
    a directory that does not exist loads nothing."""
    root = Path(directory)
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.glob("*.events.json")):
        try:
            answer = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        events = answer.get("events") if isinstance(answer, dict) else answer
        if not isinstance(events, list):
            continue
        run_id = (answer.get("run_id") if isinstance(answer, dict) else None) or path.name[: -len(".events.json")]
        stub.run_events[run_id] = events
        rows.append(_history_entry(run_id, events))
        output = root / f"{path.name[: -len('.events.json')]}.output.json"
        if output.is_file():
            try:
                text = json.loads(output.read_text(encoding="utf-8")).get("text")
            except (OSError, ValueError, AttributeError):
                text = None
            if isinstance(text, str):
                stub.run_output_text[run_id] = {"stdout": text}
    rows.sort(key=lambda row: (row.get("started_wall_utc") or "", row["run_id"]), reverse=True)
    stub.history_runs = rows + [row for row in stub.history_runs if row["run_id"] not in {entry["run_id"] for entry in rows}]
    return [row["run_id"] for row in rows]


def serve_fixture_directory(directory, *, web_build=None, port: int = 8792) -> None:
    """THE LOCAL REPLAY STAGE: the console API over a stub runtime that holds nothing but the recorded runs of one
    directory, on loopback, until interrupted. It reaches no runtime and no robot. Its credential is the tests' one,
    so it is for this machine's headless browser and nothing else."""
    from aiohttp import web

    if int(port) in RESERVED_CONSOLE_PORTS:
        raise ValueError(f"port {port} is one of the console's own ports; the replay stage never binds it")
    stub = StubRuntime(data_mode="live")
    stub.mission_state_answer = {"ok": False, "reason_code": "no_mission_recorded"}
    stub.robot_status_answer = robot_status_object(reachable=False)  # the stage reaches no robot, and says so
    loaded = load_fixture_directory(stub, directory)
    print(f"replay stage: {len(loaded)} recorded run(s) on 127.0.0.1:{port}", flush=True)
    web.run_app(make_app(stub, web_build=web_build), host="127.0.0.1", port=int(port), print=None)


# ---- the application under test ---------------------------------------------------------------------------------------------

def make_app(stub, *, web_build=None, options=None, credential=CREDENTIAL, credential_store=None, runtime_session=None):
    """The application over a stub runtime. credential_store and runtime_session are what a launch against a real
    runtime passes: with them the credential belongs to that runtime session and outlives this process."""
    return build_app(client=stub, credential=credential, web_build=web_build, host="127.0.0.1", port=0,
                     options=options or Options(), credential_store=credential_store, runtime_session=runtime_session)


def post_headers(port: int, *, origin=None, host=None, content_type="application/json", header=CONSOLE_HEADER_VALUE, credential=CREDENTIAL) -> dict:
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin is not None:
        headers["Origin"] = origin
    elif origin is None and host is None:
        headers["Origin"] = f"http://127.0.0.1:{port}"
    if content_type is not None:
        headers["Content-Type"] = content_type
    if header is not None:
        headers[CONSOLE_HEADER] = header
    if credential is not None:
        headers[CREDENTIAL_HEADER] = credential
    return headers


def body_of(text: str):
    return json.loads(text)


def contract_errors(name: str, obj) -> list:
    return demo_contracts.validate(name, obj)


async def read_frames(response, count: int, *, timeout: float = 5.0) -> list:
    """Collect SSE frames from a live response. Every data frame is unnamed, so each one is {"data": ..., "id": ...}.
    A heartbeat is an SSE comment and is returned as {"comment": "heartbeat"}; the opening `retry` field is skipped."""
    import asyncio

    frames: list = []
    current: dict = {}
    while len(frames) < count:
        line = await asyncio.wait_for(response.content.readline(), timeout=timeout)
        if not line:
            break
        text = line.decode("ascii").rstrip("\r\n")
        if text.startswith(":"):
            frames.append({"comment": text[1:].strip()})
            continue
        if text == "":
            if "data" in current:
                frames.append(current)
            current = {}
            continue
        key, _, value = text.partition(":")
        value = value.lstrip()
        if key == "data":
            current["data"] = json.loads(value)
        elif key == "id":
            current["id"] = value
    return frames


class ApiCase(unittest.IsolatedAsyncioTestCase):
    """One API over a stub runtime, on an ephemeral loopback port. The guard reads the port from the socket, so the Host
    and Origin a test sends are checked against the address the server really bound."""

    def make_stub(self) -> StubRuntime:
        return StubRuntime()

    async def asyncSetUp(self) -> None:
        self.stub = self.make_stub()
        await self.start_api()

    async def start_api(self, **kwargs) -> None:
        self.app = make_app(self.stub, **kwargs)
        self.server = TestServer(self.app, host="127.0.0.1")
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.port = self.server.port
        self.addAsyncCleanup(self.client.close)

    def headers(self, **kwargs) -> dict:
        return post_headers(self.port, **kwargs)

    async def post(self, path: str, payload=None, **kwargs):
        body = "" if payload is None else json.dumps(payload)
        return await self.client.post(path, data=body, headers=self.headers(**kwargs))

    async def json_of(self, response):
        return json.loads(await response.text())


def find_leaks(text: str) -> list:
    """Every forbidden string that really is in this text, plus anything shaped like a private value."""
    import re

    found = [name for name, value in (("address", PRIVATE_ADDRESS), ("user", USER_NAME), ("home", HOME_PATH), ("ssh", SSH_TARGET), ("camera", CAMERA_ID))
             if value in text]
    home = os.path.expanduser("~")
    if len(home) >= 4 and home in text:
        found.append("real_home")
    if re.search(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])", text):
        found.append("dotted_quad")
    return found
