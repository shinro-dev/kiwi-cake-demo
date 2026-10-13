# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.mission_service: assembling a MissionCoordinator out of what this runtime actually has.

WHAT THIS FILE IS FOR. The coordinator takes everything that touches a robot and everything that touches a Cake slot
as injected adapters, which is what makes it testable without either. Something has to build those adapters from a
runtime's mode, its configuration and its data directory, and that job is here rather than in the daemon so the
refusals can be stated once and tested without a daemon.

IT REFUSES RATHER THAN IMPROVISES. A mission needs four things and each one can be missing on its own:
  - a SYNTHETIC runtime, because the only execution adapter that exists today is the rehearsal one and that adapter is
    constructible only in a synthetic runtime (demo_runtime.mission_rehearsal says why, and enforces it);
  - a configured resident, so a slot can be read and an operation submitted;
  - at least one PREPARED activation candidate, because a mission with none could never leave WAITING_FOR_CAPABILITY;
  - a CAPABILITY BINDING, the one slot and the one capsule that decide availability. Without it nothing here can answer
    whether the capability is available, and a runtime that guessed from a slot count would report a capability that
    was never activated.
Each answer is a reason code. None of them is an error and none of them is a mission that half exists.

THE LIVE BUILDER SHIPS OFF. build_hardware_mission_coordinator is the only way a LIVE runtime gets a mission surface, and
it is asked only when the configuration's mission.execution names "proven_controller". That key ships UNSET, and unset is
exactly today's answer: mission_unavailable with rehearsal_requires_synthetic_mode. Even when it is set, every one of its
conditions has to hold (LIVE, hardware commands enabled, the robot's resident on a LOCAL link, the controller against
the motor host on loopback, a runtime supervisor, a configured resident, a prepared candidate, a capability binding the
mission target file can carry and the capability's two endpoints), and a missing one is a reason code, never a
coordinator that half exists. Its capability adapter submits an ApplyPlan only to the resident at the local admin socket
this configuration names, and only when that resident, read before every submission, reports
cake.robot.expected_build_identity. IDENTITY, STATED ONCE: the build identity identifies the SOFTWARE the resident runs;
the pin refuses a different build and nothing more. Which deployment is observed is established by the authenticated
connection to the intended robot (the ssh forward to that robot's admin socket, or the local socket on the robot) and the
session that endpoint's resident reports now. The claim and match radii (mission.radii) are handed to the robot-side
execution as they are, None included; it refuses every target while they are None (demo_runtime.mission_execution).

THE SURFACE IS SEPARATELY VERSIONED AND IS NOT CONTRACT v1. A mission is not a command and a target is not a run.
Nothing here widens the command enum, the event kinds or the snapshot; the mission carries its own
MISSION_RECORD_VERSION, exactly as cake_status carries its own version.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from . import cake_activation, cake_admin, mission, mission_rehearsal
from .config import MISSION_EXECUTION_PROVEN, mission_of, robot_link_is_local, robot_of
from .mission_coordinator import CapabilityAdapter, MissionCoordinator

#: The mission this runtime offers when a caller names no targets (a browser's Start posts an empty body): ONE block,
#: from anywhere, and the robot decides how to place it (the operator's ruling of 2026-09-26). `requires_capability`
#: "auto" (mission.REQUIRES_CAPABILITY_AUTO) means the target takes whichever placement mode its own SEARCH measures:
#: a measured ARM_ONLY runs the proven arm-only cycle and never asks for the capability; a measured BASE_ADVANCE makes
#: the mission wait for it and say why, then respawn the same target carrying it. The two-cube mission of 2026-09-23
#: (a reachable cube declared false and a far cube declared true) is reachable only by naming both targets.
DEFAULT_TARGETS = (
    {"target_id": "cube", "label": "the block", "reachable": True, "requires_capability": mission.REQUIRES_CAPABILITY_AUTO},
)

#: How long one ApplyPlan submission may take. It is deliberately SHORTER than the console API's own unary timeout
#: (console_api.__main__.RUNTIME_TIMEOUT_S, 10 s) so the answer reaches the browser instead of being lost on the way
#: back. A submission that outlives it is recorded as a REQUEST failure, which is not Cake judging the candidate: the
#: operation may still be running, and the mission confirms by re-reading the slot rather than by resubmitting.
ACTIVATION_TIMEOUT_S = 8.0

#: How long one slot read may take. A read is one short child process and it is on the mission's polling path.
SLOT_READ_TIMEOUT_S = 3.0

#: Why there is no mission surface in this runtime. Each is a CODE: none names a path, a value or a machine.
NOT_SYNTHETIC = mission_rehearsal.REASON_NOT_SYNTHETIC
NOT_CONFIGURED = "cake_not_configured"
ROBOT_CONFIGURED = "rehearsal_refuses_robot_resident"

#: Why a rehearsal's ApplyPlan was not submitted. A rehearsal submits ONLY when the configuration names
#: cake.rehearsal_fixture_build_identity AND the resident answering reports exactly that build identity, read before EVERY
#: submission from what answers. That pin says which SOFTWARE answered, and refuses a different build; it cannot say which
#: machine did (a fixture resident built from the robot's source reports the robot's build identity: handoff 12.4). Which
#: deployment answers is the one at the socket the configuration names. A rehearsal configuration may not carry cake.robot
#: (ROBOT_CONFIGURED), so it cannot use the forward this runtime would make to the robot; nothing checks that the socket it
#: does name is not a forward to the robot made some other way, or the robot's own socket, and the build pin cannot tell
#: them apart either. That the socket is a fixture's rests on the configuration.
FIXTURE_NOT_CONFIGURED = "rehearsal_fixture_not_configured"
NOT_THE_FIXTURE = "rehearsal_resident_not_the_fixture"
BUILD_UNREADABLE = "resident_build_identity_unreadable"
_BUILD_IDENTITY = re.compile(r"[0-9a-f]{64}")
NO_CANDIDATE = "no_activation_candidate_prepared"
NO_CAPABILITY = "capability_not_configured"

#: A rehearsal refuses a run_target that starts a controller (mission_execution.ProvenTargetRunner says it does).
HARDWARE_RUN_TARGET_REFUSED = "rehearsal_refuses_hardware_run_target"

#: Why a LIVE runtime has no hardware mission surface. Each is one condition of build_hardware_mission_coordinator.
LIVE_MODE = "live"  # demo_runtime.daemon.LIVE; a test keeps the two equal rather than importing the daemon here
HARDWARE_NOT_LIVE = "hardware_mission_requires_live_mode"
EXECUTION_NOT_CONFIGURED = "mission_execution_not_configured"
HARDWARE_COMMANDS_OFF = "hardware_commands_not_enabled"
ROBOT_NOT_CONFIGURED = "robot_not_configured"
ROBOT_LINK_NOT_LOCAL = "robot_link_not_local"
NO_SUPERVISOR = "runtime_supervisor_unavailable"
CONTROLLER_HOST_NOT_LOOPBACK = "controller_host_not_loopback"
CAPABILITY_NOT_BINDABLE = "capability_binding_not_bindable"
NO_CAPABILITY_ENDPOINTS = "capability_endpoints_not_configured"
#: Why a hardware mission's ApplyPlan was not submitted: the resident answering at the robot's local admin socket does not
#: report the pinned build (the codes keep their names; the check is a build pin, and the local socket, with the session
#: its resident reports now, says which deployment answered).
ROBOT_BUILD_NOT_PINNED = "robot_build_identity_not_configured"
NOT_THE_ROBOT = "resident_not_the_robot"
#: The address the controller must reach the motor host on when it runs on the robot beside it (a capability target
#: refuses anything else: run_single_cube_cycle.mission_refusal, CAPABILITY_HOST).
CONTROLLER_LOOPBACK = "127.0.0.1"
_SLOT_UUID = re.compile(r"[0-9a-f]{32}")
_CAPSULE = re.compile(r"[0-9a-f]{64}")

#: The shape a caller-supplied target id and label may take. A target id is written into a durable record and shown, so
#: its alphabet is an allowlist with no separator in it rather than a list of characters to refuse.
_TARGET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_MAX_LABEL = 120
_MAX_TARGETS = 8
_TARGET_KEYS = ("target_id", "label", "reachable", "requires_capability")


class TargetsRefused(ValueError):
    """The targets a caller supplied are not a mission. The message is a reason code and echoes no value."""


def normalize_targets(raw) -> list:
    """The targets to start, from what a caller sent, or TargetsRefused.

    A caller may name targets and a browser is a caller, so this is a boundary: the values end up in an append-only
    record that is read back and shown, and nothing that is not one of the four fields below reaches it. None means
    DEFAULT_TARGETS, the one auto target. `reachable` is a boolean; `requires_capability` is true, false or exactly
    "auto" (never coerced: the word is carried as it was declared, and a console reads one of the three).
    """
    if raw is None:
        return [dict(target) for target in DEFAULT_TARGETS]
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_TARGETS:
        raise TargetsRefused("targets_not_a_list")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or not set(item) <= set(_TARGET_KEYS):
            raise TargetsRefused("target_not_an_object")
        target_id = item.get("target_id")
        if not isinstance(target_id, str) or _TARGET_ID.fullmatch(target_id) is None or target_id in seen:
            raise TargetsRefused("target_id_not_allowed")
        seen.add(target_id)
        label = item.get("label", target_id)
        if not isinstance(label, str) or len(label) > _MAX_LABEL:
            raise TargetsRefused("target_label_not_allowed")
        if "reachable" in item and not isinstance(item["reachable"], bool):
            raise TargetsRefused("target_flag_not_allowed")
        requires = item.get("requires_capability", False)
        if not (isinstance(requires, bool) or requires == mission.REQUIRES_CAPABILITY_AUTO):
            raise TargetsRefused("target_flag_not_allowed")
        out.append({"target_id": target_id, "label": label, "reachable": bool(item.get("reachable", True)), "requires_capability": requires})
    return out


def prepared_candidates(config) -> cake_activation.PreparedCandidates:
    """The candidates this runtime is willing to activate, read from disk ONCE, here. A configuration that names none
    produces an empty set, which is a runtime that activates nothing."""
    cake = getattr(config, "cake", None)
    named = getattr(cake, "activation_candidates", None) if cake is not None else None
    roles = getattr(cake, "candidate_roles", None) if cake is not None else None
    if isinstance(cake, dict):
        named, roles = cake.get("activation_candidates"), cake.get("candidate_roles")
    # Roles only when configured: without them the candidates are served by key and digest, as before.
    return cake_activation.PreparedCandidates(dict(named) if isinstance(named, dict) else {},
                                              roles=dict(roles) if isinstance(roles, dict) and roles else None)


def capability_binding(config):
    """(name, slot_uuid, active_capsule) or None. Read the same way from a CakeConfig or from a test's plain mapping,
    exactly as cake_admin.configured_paths reads the two paths."""
    cake = getattr(config, "cake", None)
    if cake is None:
        return None
    binding = cake.get("capability") if isinstance(cake, dict) else getattr(cake, "capability", None)
    if binding is None:
        return None
    if isinstance(binding, dict):
        name, slot_uuid, capsule = binding.get("name"), binding.get("slot_uuid"), binding.get("active_capsule")
    else:
        name, slot_uuid, capsule = binding.name, binding.slot_uuid, binding.active_capsule
    if not all(isinstance(value, str) and value for value in (name, slot_uuid, capsule)):
        return None
    return (name, slot_uuid, capsule)


def refuse_unless_fixture(probe_path: str, socket_path: str, *, fixture_build_identity: Optional[str],
                          timeout_s: float = SLOT_READ_TIMEOUT_S) -> Optional[dict]:
    """None when the resident answering at `socket_path` reports the rehearsal fixture's pinned build, else the
    REQUEST_FAILED answer a submission gets instead of being made.

    WHAT ANSWERS, NOT WHAT IS CONFIGURED, AND POSITIVE. This reads what actually answers (get-status, through the
    read-only allowlist) and passes ONLY a resident whose build_identity equals the configured
    cake.rehearsal_fixture_build_identity. No fixture named, a build identity that differs, none that can be read, or no
    answer: refused, and nothing is submitted. The build identity identifies the SOFTWARE; which deployment answered is
    the one at `socket_path`, the connection this configuration names. A rehearsal configuration may not carry
    cake.robot, and nothing here checks that `socket_path` is not a forward to the robot made some other way: that it
    is a fixture's socket rests on the configuration (FIXTURE_NOT_CONFIGURED's comment). It is a separate connection
    from the submission that follows, so it says what answered a moment before, not during."""
    return _refuse_unless_build(probe_path, socket_path, expected=fixture_build_identity,
                                not_configured=FIXTURE_NOT_CONFIGURED, mismatch=NOT_THE_FIXTURE, timeout_s=timeout_s)


def refuse_unless_robot(probe_path: str, socket_path: str, *, robot_build_identity: Optional[str],
                        timeout_s: float = SLOT_READ_TIMEOUT_S) -> Optional[dict]:
    """refuse_unless_fixture's rule for a HARDWARE mission: None only when the resident answering at `socket_path` reports
    exactly cake.robot.expected_build_identity, read from what answers immediately before the submission. Positive: a
    resident is never submitted to because it failed to look like something else. The pin says which SOFTWARE answered;
    that it is the intended robot's resident is established by `socket_path` itself, the robot's local admin socket on
    the robot this runtime runs on, and by the session that resident reports now."""
    return _refuse_unless_build(probe_path, socket_path, expected=robot_build_identity,
                                not_configured=ROBOT_BUILD_NOT_PINNED, mismatch=NOT_THE_ROBOT, timeout_s=timeout_s)


def _refuse_unless_build(probe_path: str, socket_path: str, *, expected: Optional[str], not_configured: str,
                         mismatch: str, timeout_s: float) -> Optional[dict]:
    def refused(reason: str) -> dict:
        return {"outcome": cake_activation.REQUEST_FAILED, "detail": reason, "status": None, "transaction_phase": None,
                "transaction_uuid": None, "reason_code": reason}

    if not isinstance(expected, str) or _BUILD_IDENTITY.fullmatch(expected) is None:
        return refused(not_configured)
    status = cake_admin.run_probe(probe_path, socket_path, "get-status", timeout_s=timeout_s)
    answered = status.get("fields", {}).get("build_identity") if status.get("ok") else None
    if not isinstance(answered, str) or _BUILD_IDENTITY.fullmatch(answered) is None:
        return refused(BUILD_UNREADABLE)
    if answered != expected:
        return refused(mismatch)
    return None


def capability_adapter(*, probe_path: str, socket_path: str, slot_uuid: str, active_capsule: str,
                       candidates: cake_activation.PreparedCandidates,
                       read_timeout_s: float = SLOT_READ_TIMEOUT_S,
                       submit_timeout_s: float = ACTIVATION_TIMEOUT_S,
                       fixture_build_identity: Optional[str] = None,
                       robot_build_identity: Optional[str] = None) -> CapabilityAdapter:
    """The Cake-facing half of a mission, bound to ONE slot and ONE capsule.

    `available` and `observe` are the SAME read deliberately, and they are two callables deliberately. They are the
    same read because there is only one thing that says the capability is usable: that slot, read back. They are kept
    apart on the adapter because the coordinator uses them for two different questions - may this target proceed, and
    did the transaction that just completed actually move the slot - and collapsing them would let an answer about a
    transaction stand in for an observation.
    """

    def slot_is_active() -> bool:
        listing = cake_activation.read_slots_text(probe_path, socket_path, timeout_s=read_timeout_s)
        return cake_activation.capability_is_active(listing, slot_uuid=slot_uuid, active_capsule=active_capsule)

    def read_slot() -> dict:
        # The same list-slots read, with what it said kept: an activation records this before its submit and after
        # its answer, and decides OBSERVED from the capability_active of the very reading it records. Read-only, through
        # cake_admin's allowlist like every read here.
        listing = cake_activation.read_slots_text(probe_path, socket_path, timeout_s=read_timeout_s)
        return cake_activation.slot_reading(listing, slot_uuid=slot_uuid, active_capsule=active_capsule)

    def submit(operation_uuid: str, payload: bytes) -> dict:
        # BEFORE EVERY ApplyPlan: the resident that answers at this socket must report the pinned build of the named
        # fixture (refuse_unless_fixture) or, for a hardware mission, of the robot (refuse_unless_robot). Exactly one of the
        # two is asked. A build pin refuses a different build; the socket is what says which deployment answers.
        if robot_build_identity is not None:
            refused = refuse_unless_robot(probe_path, socket_path, robot_build_identity=robot_build_identity,
                                          timeout_s=read_timeout_s)
        else:
            refused = refuse_unless_fixture(probe_path, socket_path, fixture_build_identity=fixture_build_identity,
                                            timeout_s=read_timeout_s)
        if refused is not None:
            return refused
        return cake_activation.submit_apply_plan(probe_path, socket_path, operation_uuid, payload,
                                                 timeout_s=submit_timeout_s)

    return CapabilityAdapter(available=slot_is_active, submit=submit, observe=slot_is_active, candidates=candidates,
                             read_slot=read_slot)


def build_mission_coordinator(*, mode: str, config, data_dir, run_target=None) -> tuple:
    """(coordinator, candidates, reason_code). Two of the three are always None together.

    THE MODE IS CHECKED FIRST AND IT IS NOT THIS FUNCTION'S OWN CHECK. `mission_rehearsal.recording_execution` refuses
    outside a synthetic runtime and is the only constructor of an adapter whose confirmation is a constant; the check
    is repeated here only so that a live runtime answers a reason code instead of raising, and removing the line below
    would not make a live runtime able to obtain one.

    Nothing is opened until everything is present: the missions file is created by the coordinator's own journal, so a
    runtime that cannot build one leaves no file behind to be read back as an empty mission.
    """
    # A REHEARSAL NEVER CARRIES A TARGET WITH A CONTROLLER. A run_target that says it starts one (mission_execution's
    # ProvenTargetRunner, or run_proven_target itself) is refused before anything else, in every mode, so no caller can
    # pair the rehearsal's unconditional confirmations with a path that moves a robot.
    if _is_hardware_run_target(run_target):
        return (None, None, HARDWARE_RUN_TARGET_REFUSED)
    try:
        execution, _transcript = mission_rehearsal.recording_execution(mode=mode)
    except mission_rehearsal.RehearsalNotAvailable:
        return (None, None, NOT_SYNTHETIC)
    # A rehearsal never gets the robot's forward. The daemon already refuses to build a synthetic runtime with
    # cake.robot; this is the same rule at the one place a rehearsal coordinator is built, so no other caller can hand
    # it a configuration that carries the robot section and the forward this runtime would make from it. What it does
    # not catch: a cake.admin_socket_path that is a forward to the robot made some other way (FIXTURE_NOT_CONFIGURED's
    # comment); nothing here compares the socket with the robot's.
    if robot_of(config) is not None:
        return (None, None, ROBOT_CONFIGURED)
    probe_path, socket_path = cake_admin.configured_paths(config)
    if probe_path is None or socket_path is None:
        return (None, None, NOT_CONFIGURED)
    candidates = prepared_candidates(config)
    if not candidates.keys():
        return (None, None, NO_CANDIDATE)
    binding = capability_binding(config)
    if binding is None:
        return (None, None, NO_CAPABILITY)
    _name, slot_uuid, active_capsule = binding
    # The rehearsal's adapter submits, before every ApplyPlan, only to a resident proving it is the named fixture.
    cake = getattr(config, "cake", None)
    fixture = cake.get("rehearsal_fixture_build_identity") if isinstance(cake, dict) else getattr(cake, "rehearsal_fixture_build_identity", None)
    capability = capability_adapter(probe_path=probe_path, socket_path=socket_path, slot_uuid=slot_uuid,
                                    active_capsule=active_capsule, candidates=candidates,
                                    fixture_build_identity=fixture)
    coordinator = MissionCoordinator(os.path.join(os.fspath(data_dir), mission.MISSIONS_FILE_NAME),
                                     execution, capability, data_mode=mode, run_target=run_target)
    return (coordinator, candidates, None)


def _is_hardware_run_target(run_target) -> bool:
    if run_target is None:
        return False
    return bool(getattr(run_target, "hardware_run_target", False)
                or getattr(getattr(run_target, "func", None), "hardware_run_target", False))


def capability_endpoints(config) -> Optional[dict]:
    """What a capability target's mission target file names (target_binding.CapabilityEndpoints), or None when any part
    of it is missing or has a shape that file refuses. The admin paths are the ones this runtime itself reads."""
    probe_path, socket_path = cake_admin.configured_paths(config)
    binding = capability_binding(config)
    section = mission_of(config)
    if probe_path is None or socket_path is None or binding is None or section is None:
        return None
    _name, slot_uuid, capsule = binding
    observation, command = section.capability_observation_endpoint, section.capability_command_endpoint
    if observation is None or command is None or observation == command:
        return None
    return {"admin_probe": probe_path, "admin_socket": socket_path, "slot_uuid": slot_uuid, "capsule": capsule,
            "observation_endpoint": observation, "command_endpoint": command}


def build_hardware_mission_coordinator(*, mode: str, config, data_dir, supervisor, hardware_enabled: bool) -> tuple:
    """(coordinator, candidates, reason_code) for a LIVE runtime on the robot, or a reason code and nothing else.

    EVERY CONDITION IS REQUIRED, checked in this order, and the first missing one is the answer:
      - the runtime is LIVE (a synthetic runtime has the rehearsal and nothing else);
      - mission.execution is "proven_controller". It ships UNSET; nothing in this repository sets it;
      - hardware commands are enabled (the same switch that gates every motion command);
      - the robot's resident is configured and reached on a LOCAL link: the daemon runs on the robot, beside the
        resident, the host and the capability (section 8 of the handoff), so a desktop runtime can never carry one;
      - the runtime has a supervisor, because every target is a supervised run started through its admission;
      - the controller reaches the motor host on loopback, which a capability target requires;
      - a resident to read, a prepared activation candidate and a capability binding, as for a rehearsal, with the binding
        in the shape the mission target file carries (a 32-hex slot uuid and a 64-hex capsule digest);
      - the capability's observation and command endpoints.
    What is built: HardwareExecution with the configuration's mission.radii (None stays None, and every target then fails
    before anything runs; mission_execution says why), a ProvenTargetRunner as the coordinator's run_target with the
    mission evidence file (mission.MISSION_EVIDENCE_FILE_NAME, created by its first record), and a capability adapter whose
    ApplyPlan is submitted only after the resident answering at the robot's local admin socket reports
    cake.robot.expected_build_identity (a build pin: it names the software; the local socket, and the session its
    resident reports now, say which deployment answered). The rehearsal adapter is not reachable from here.
    """
    from . import mission_execution  # imported HERE: a runtime that never builds one never loads it

    if mode != LIVE_MODE:
        return (None, None, HARDWARE_NOT_LIVE)
    section = mission_of(config)
    if section is None or section.execution != MISSION_EXECUTION_PROVEN:
        return (None, None, EXECUTION_NOT_CONFIGURED)
    if not hardware_enabled:
        return (None, None, HARDWARE_COMMANDS_OFF)
    robot = robot_of(config)
    if robot is None:
        return (None, None, ROBOT_NOT_CONFIGURED)
    if not robot_link_is_local(config):
        return (None, None, ROBOT_LINK_NOT_LOCAL)
    if supervisor is None:
        return (None, None, NO_SUPERVISOR)
    if getattr(getattr(config, "host", None), "address", None) != CONTROLLER_LOOPBACK:
        return (None, None, CONTROLLER_HOST_NOT_LOOPBACK)
    probe_path, socket_path = cake_admin.configured_paths(config)
    if probe_path is None or socket_path is None:
        return (None, None, NOT_CONFIGURED)
    candidates = prepared_candidates(config)
    if not candidates.keys():
        return (None, None, NO_CANDIDATE)
    binding = capability_binding(config)
    if binding is None:
        return (None, None, NO_CAPABILITY)
    _name, slot_uuid, active_capsule = binding
    if _SLOT_UUID.fullmatch(slot_uuid) is None or _CAPSULE.fullmatch(active_capsule) is None:
        return (None, None, CAPABILITY_NOT_BINDABLE)
    endpoints = capability_endpoints(config)
    if endpoints is None:
        return (None, None, NO_CAPABILITY_ENDPOINTS)
    capability = capability_adapter(probe_path=probe_path, socket_path=socket_path, slot_uuid=slot_uuid,
                                    active_capsule=active_capsule, candidates=candidates,
                                    robot_build_identity=robot.expected_build_identity)
    hardware = mission_execution.HardwareExecution(radii=section.radii)
    evidence = mission_execution.EvidenceJournal(os.path.join(os.fspath(data_dir), mission.MISSION_EVIDENCE_FILE_NAME))
    runner = mission_execution.ProvenTargetRunner(hardware=hardware, supervisor=supervisor, capability_endpoints=endpoints,
                                                  evidence=evidence)
    coordinator = MissionCoordinator(os.path.join(os.fspath(data_dir), mission.MISSIONS_FILE_NAME),
                                     hardware.adapter(), capability, data_mode=mode, run_target=runner)
    runner.bind_recorder(coordinator)
    return (coordinator, candidates, None)


def unavailable(reason_detail: Optional[str]) -> dict:
    """The one answer every mission op gives when this runtime has no mission surface.

    ONE reason_code, so a client has one thing to branch on, and a `detail` that says WHICH of the four is missing,
    because "unavailable" with no reason cannot be told from "broken" and an operator is owed the difference.
    """
    return {"ok": False, "reason_code": "mission_unavailable", "detail": reason_detail}
