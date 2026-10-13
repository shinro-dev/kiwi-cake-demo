# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.app: the routes. Every one of them is a forward or a read model; none of them decides anything.

WHAT EACH ANSWER IS. A command always comes back as the runtime's own contract command_result, with HTTP 200, because a
refusal is an answer and not a transport failure: status 'rejected' with the runtime's reason_code is what the operator
needs to see. HTTP 4xx and 5xx are kept for the things that never reached the runtime at all: a request that failed the
guard (403), a body that is not a command (400), a runtime that cannot be reached (503). The one shape a client must
handle for every command is the command_result.

THE ONLY WAY TO RESOLVE A LOST ANSWER is GET /api/commands/{command_id}. A command is never sent twice by this process,
and a client must never resend one blindly: a submit whose answer was lost comes back as status 'uncertain' with
reason_code 'response_lost', which is a question to ask, not a command to repeat.

WHEN THE RUNTIME ITSELF CANNOT SAY what became of a command, asking by id answers 'uncertain' for as long as that holds,
and the runtime blocks every further command until the question is settled. POST /api/commands/{command_id}/resolve is
how that question is put back to the runtime. It is NOT a retry and it is NOT a way to clear the journal: the runtime
re-reads the machine evidence itself, records the operator's declared finding as an ATTESTATION beside that evidence,
and decides on its own whether the block lifts. This process forwards the request and the answer and decides neither.

TWO FACTS ABOUT A STOP, AND A THIRD THIS PROCESS CANNOT STATE. POST /api/stop reads its body now. A body that is a
contract stop command is SUBMITTED, so the runtime journals it under the id the browser minted and answers a repeat of
that id with the recorded result instead of stopping twice; a body that carries no command (a command line client
sending {}) keeps the runtime's plain stop op, which journals nothing and is idempotent only in the sense that stopping
a stopped run does nothing. Either way the answer says whether the RUNTIME took the request. Whether the robot actually
stopped is a third fact and it is not here: the contract's stop.requested, stop.acknowledged and stop.outcome records
are what say that, and an HTTP 200 from this route never does.

THE STOP ENDS A MISSION TOO, AND SAYS SO APART. The runtime's one stop path stops the controller run first and then the
mission, and the answer of /api/stop carries the mission's part under `mission` beside the run's: which mission, whether
its stop was asked for, and the state it had folded to when the runtime answered. It is a request's answer. It never
says that motion ceased, and `ok` stays what it always was, whether a RUN was stopped. A submitted stop's mission part
arrives beside the runtime's command_result and is taken off it here, so `command` stays exactly a contract
command_result and so does the answer of POST /api/commands for a stop.

A REUSED COMMAND ID IS ANSWERED, NOT GUESSED AT. The runtime's receipt carries the digest of the command it recorded
under that id. When a submit comes back with a digest that is not the digest of the body just sent, the two are not the
same request: the runtime started nothing (it answered from its journal) and this process says 409 with
command_id_reused rather than handing back the first command's outcome as if it were this one's. A receipt without a
digest (a command the runtime rejected records no command object) says the journal cannot tell, and that is reported as
what it is rather than read as agreement.

THE MISSION IS A SURFACE OF ITS OWN AND IS NOT CONTRACT v1. A mission is not a command and a target is not a run, so the
six /api/mission routes add no command type, no event kind and no snapshot key; the view they forward carries its own
mission_record_version, exactly as /api/cake carries its own cake_status_version. The two reads take no body. The four
POSTs pass the SAME guard POST /api/commands passes, through the same function, so there is no weaker path to any of
them, and POST /api/mission/activate refuses a body carrying any key besides candidate_key: the bytes of a candidate
belong to the runtime, which read them once from its private configuration, and nothing a browser sends can name a
path, a socket or an admin operation. POST /api/mission/retry takes a body of at most {mission_id}, the hold the
operator saw, and names no target, attempt or action: the hold on the runtime's record decides all three.

THE ROBOT'S RESIDENT IS A SURFACE OF ITS OWN TOO. GET /api/robot forwards the runtime's robot_status, which carries its
own robot_status_version. POST /api/robot/confirm-session GRANTS permission to move, so it passes the same guard as
POST /api/commands through _command_body(), takes a body of exactly two keys (session_uuid, host_instance, the latter
exactly {slot_instance, spawn_sequence, pid}), and is refused by the runtime unless every component is what the robot
reports now: the resident's session and its supervisor slot's host instance.

THE CAPABILITY SLOT CHECK IS A SURFACE OF ITS OWN, AND MOVES NO MOTOR. GET /api/capability/check forwards the runtime's
capability_check_state (its own capability_check_version): the prepared candidates by key and digest, the robot's
resident as the cached reading reports it, the first reason an apply would be refused now and every check on the record.
POST /api/capability/check submits ONE prepared Plan to the robot's capability slot and reads it back: it passes the same
guard as POST /api/commands through _command_body(), the same replay block and the same runtime-session check, and takes a
body of exactly three keys (candidate_key, payload_digest, session_uuid). There is no field for bytes, a path or an
operation. The runtime refuses it unless a fresh read reports the pinned build, exactly that session and a HELD host (no
host process under the supervisor slot, HEALTHY or not).

THE SIGNATURE POLICY CHECK IS A SURFACE OF ITS OWN, MOVES NO MOTOR AND TOUCHES NO SLOT. GET /api/capability/signature
forwards the runtime's capability_signature_state (its own signature_check_version): whether a run is in flight, the first
reason one would be refused now, the policy and every run on the record. POST /api/capability/signature runs the release's
own verifier on the robot over every deployed capsule (under the resident's trusted keys, a throwaway key and any unsigned
twin) and answers the verdict table: it passes the same guard as POST /api/commands through _command_body(), the same
replay block and the same runtime-session check, and takes a body of EXACTLY nothing ({}): there is no field for a
capsule, a key, a path or a policy to arrive in. This is the release verifier under the resident's policy; the resident
admits capsules only when it starts, and nothing here claims a live admission.

THE LAST LINE. Every JSON body leaves through one function that runs the runtime's own Sanitizer over it with this
process's private strings (its paths, its socket, its credential) and the generic shapes (any absolute path, any dotted
quad, any hardware address, any user@host). The runtime already sanitizes what it serves; this repeats the work one layer
out, because each layer can only be sure of what it owns, and the API's own error paths know strings the runtime does not.
A STREAMED body is a JSON body as well, so _sanitizer_of() is handed to every sse pump: the SSE frames used to skip this
step entirely, and text that GET /api/history/<run>/events removed came out whole on GET /api/events.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import secrets
import uuid
from typing import Callable, Optional

from aiohttp import web

from . import API_VERSION, LOOPBACK, repository_root
from .bridge import Bridge, ReadOnlyClient, RuntimeUnavailable, StreamEnded
from .bridge import DEFAULT_QUEUE
from .security import CredentialError, CredentialStore, Guard
from .sse import HEARTBEAT_S, WRITE_TIMEOUT_S, Channel, SlowReader, limit_write_buffer, render_event, telemetry_renderer
from .sse import pump as sse_pump
from .web import CONTENT_SECURITY_POLICY, WebBuild

import demo_contracts
from demo_runtime.journal import payload_digest
from demo_runtime.read_models import REDACTION, Sanitizer
from demo_runtime.robot_link import host_instance_of

CONTRACT_VERSION = demo_contracts.CONTRACT_VERSION

PREVIEW_ROLES = ("wrist", "front")
PREVIEW_FIRST_FRAME_S = 1.5
OBSERVATION_KINDS = ("record", "perception")
FRAME_KINDS = ("raw", "overlay")
IMMUTABLE = "private, max-age=31536000, immutable"
# How long a page load waits for the runtime to name its session before the page is served with what is in hand. It is
# short on purpose: a runtime that hangs must not keep the console from loading, and the page is told whose credential
# it holds (`bound_runtime_session` in the boot block) so that it can see a later rotation for itself.
INDEX_SESSION_WAIT_S = 2.0

STATUS_FOR_REASON = {
    "not_found": 404, "unknown_run": 404, "unknown_command": 404, "bad_request": 400,
    "observation_source_unavailable": 503, "replay_only": 409, "hello_required": 403,
    "contract_incompatible": 409, "internal_error": 502, "unknown_op": 502,
    # The id names a command this runtime already holds and the body is not the one it holds: a conflict about the
    # state of the world, not a malformed request. Nothing was started; the runtime answered out of its journal.
    "command_id_reused": 409,
    # The runtime answering now is not the one this API bound its credential and its readiness to.
    "runtime_session_changed": 409,
    # The runtime's two resolve refusals that are about the WORLD and not about the request: a controller of that
    # command is alive, or the command is not uncertain at all. Both are conflicts, not malformed requests. The other
    # two ('finding_not_allowed', 'note_not_allowed') are about the body and take the default 400.
    "controller_alive": 409, "command_not_uncertain": 409,
    # The mission's refusals. Four of them are about the STATE OF THE WORLD and not about the request, so they are
    # conflicts: this runtime has no mission surface at all, one mission is already running, the mission is not
    # waiting for a capability, or no mission has been recorded yet. 'unknown_candidate' is about the body and takes
    # the default 400, which is what naming a candidate this runtime did not prepare is.
    "mission_unavailable": 409, "mission_already_running": 409, "mission_not_waiting_for_capability": 409,
    "no_mission_recorded": 404, "unknown_mission": 404,
    # Three more about the world: an activation for a mission whose stop was asked for (nothing was submitted), an
    # activation for a mission that already has an operation with the resident (nothing was submitted: a second press
    # is never a second ApplyPlan), and a mission start while a controller run holds the single run slot. A stop with
    # nothing to stop is NOT here: the runtime answers it with ok true, stopping false and reason_code
    # no_running_mission, which is a 200, because nothing went wrong and there was simply no mission to stop.
    "mission_stopping": 409, "activation_in_flight": 409, "run_active": 409,
    # A Retry's refusals are all about the HOLD, never about the body: the mission is not held for the operator, nothing
    # carries it any more, the held attempt offers no retry, this hold's Retry was already asked for, or the run target
    # cannot park the arm first. A LIVE runtime also refuses every Retry unless its configuration sets mission.retry and
    # names the attended label (mission_retry_not_enabled, mission_retry_not_attended). Nothing was written and nothing
    # was started.
    "mission_not_held": 409, "mission_not_carried": 409, "mission_retry_not_offered": 409,
    "mission_retry_already_requested": 409, "mission_park_unavailable": 409,
    "mission_retry_not_enabled": 409, "mission_retry_not_attended": 409,
    # A Retry (and a mission start that can move the robot) is also asked the supervisor's block() before anything is
    # written: a controller this runtime did not start is running, or the hardware command path is off. Both are about
    # the world; run_active and owner_unresolved are listed with the other conflicts.
    "owner_external": 409, "hardware_commands_not_enabled": 409,
    # The robot session confirmation's refusals about the WORLD: no robot is configured, the robot cannot be read
    # right now, or the session the operator saw is not the one it reports now. A malformed body keeps the 400.
    "robot_not_configured": 409, "robot_unreachable": 409, "robot_session_mismatch": 409,
    "robot_host_pid_mismatch": 409, "robot_host_spawn_sequence_mismatch": 409, "robot_host_slot_instance_mismatch": 409,
    "robot_host_not_running": 409, "robot_host_unreadable": 409,
    "robot_resident_not_the_robot": 409, "robot_build_identity_unreadable": 409,
    "robot_status_invalid": 502,
    # The capability slot check's refusals about the WORLD: the runtime has no check (not LIVE, or no prepared candidate),
    # one is already in flight, a mission or an unresolved command holds the run slot, the named digest is not what the
    # runtime holds for that key, a host runs under the supervisor slot (the check keeps it held), or the capability slot
    # was read from another resident session. An unknown candidate is about the body and keeps the default 400.
    "capability_check_unavailable": 409, "capability_check_in_flight": 409, "mission_running": 409, "owner_unresolved": 409,
    "payload_digest_mismatch": 409, "robot_host_not_held": 409, "capability_slot_session_mismatch": 409,
    # The signature policy check's refusals about the WORLD: the runtime has no check (not LIVE, not on the robot, or not
    # configured for one), one is already in flight, or the deployed release cannot be verified as one under require
    # (no verifier binary, an unreadable manifest or resident.conf, a policy that is not require, no trusted key, no
    # capsule named, no scratch directory). A body that carries anything keeps the default 400.
    "signature_check_unavailable": 409, "signature_check_in_flight": 409, "verifier_binary_unavailable": 409,
    "release_manifest_unreadable": 409, "resident_conf_unreadable": 409, "resident_policy_not_require": 409,
    "resident_trusted_keys_unreadable": 409, "manifest_names_no_capsule": 409, "scratch_directory_unavailable": 409,
}
REASON_FOR_STATUS = {404: "not_found", 405: "method_not_allowed", 400: "bad_request", 413: "body_too_large", 415: "content_type_not_json"}
# The two shapes a capability slot check's body carries besides its key: the digest cake_activation prints for a prepared
# candidate, and a resident session as the robot surface shows it. Checked here so a body that cannot be one never travels.
_PAYLOAD_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_RESIDENT_SESSION = re.compile(r"[0-9a-f]{8,64}")


class OutgoingSanitizer(Sanitizer):
    """The runtime's sanitizer with one shape moved to the front.

    MEASURED, not assumed: Sanitizer removes a dotted quad BEFORE it looks for user@host, so 'name@<an address>' loses the
    address and keeps the name, which is a user name on an operator's screen. Here the pair is taken whole first, and
    everything else is the runtime's own code, unchanged.

    What this cannot do: a device id, a robot id or a camera id has no generic shape, so nothing here recognises one. That
    is the RUNTIME's allowlist and its config-derived tokens, which run before a value ever reaches this process. The
    live test proves it at the far end, on the fake controller's own trap fields.
    """

    _PAIR = re.compile(r"[\w.+-]+@[\w.-]+")

    def text(self, value: str, *, generic: bool = False) -> str:
        if generic:
            value = self._PAIR.sub(REDACTION, value)
        return super().text(value, generic=generic)

    def remember(self, token: str) -> None:
        """Add one more private string to remove. The credential this process serves can change while it runs (it
        follows the runtime session), and a value that is not in this list is a value that would leave whole."""
        if not isinstance(token, str) or len(token) < 4:
            return
        current = tuple(self._tokens)
        if token not in current:
            self._tokens = sorted({*current, token}, key=len, reverse=True)


class ApiState:
    """The little this process remembers. None of it is a run, a lease or a capability.

    IT DOES REMEMBER WHICH RUNTIME IT IS TALKING TO, because the credential belongs to that runtime's session and not
    to this process. bind() is called with every session this process learns: from the handshake, and from the
    runtime_session inside every snapshot, which is what makes a replaced runtime noticed within one poll rather than
    at the next reload. Binding to a session this process has not seen before takes that session's credential from the
    store, and the guard then serves and accepts that one. Nothing is invented when the store cannot answer: the
    credential in hand stays, and hello reports who owns it."""

    OWNER_SESSION = "runtime_session"
    OWNER_PROCESS = "api_process"

    def __init__(self, guard: Guard, *, store: Optional[CredentialStore] = None, runtime_session: Optional[str] = None,
                 sanitizer: Optional[OutgoingSanitizer] = None):
        self.session = f"api-{uuid.uuid4().hex[:12]}"
        self.data_mode: Optional[str] = None  # the last data_mode the runtime reported; used ONLY to refuse, never to allow
        self.runtime_session: Optional[str] = runtime_session
        self.credential_owner = self.OWNER_SESSION if (store is not None and runtime_session) else self.OWNER_PROCESS
        self.session_changes = 0
        self._guard, self._store, self._sanitizer = guard, store, sanitizer

    def note(self, snapshot) -> None:
        if not isinstance(snapshot, dict):
            return
        if isinstance(snapshot.get("data_mode"), str):
            self.data_mode = snapshot["data_mode"]
        self.bind(snapshot.get("runtime_session"))

    def bind(self, runtime_session) -> bool:
        """Follow the runtime session this process is serving. True when the credential really changed."""
        if self._store is None or not isinstance(runtime_session, str) or runtime_session == "":
            return False
        if runtime_session == self.runtime_session:
            return False
        try:
            credential = self._store.credential_for(runtime_session)
        except (CredentialError, OSError):
            # The store could not answer. The credential in hand is the one that was already served to a page, so it
            # stays; saying so is better than minting a value nothing has ever been given.
            return False
        if self.runtime_session is not None:
            self.session_changes += 1
        self.runtime_session = runtime_session
        self.credential_owner = self.OWNER_SESSION
        rotated = self._guard.rotate(credential)
        if rotated and self._sanitizer is not None:
            self._sanitizer.remember(credential)
        return rotated

    @property
    def replay_only(self) -> bool:
        return self.data_mode == "replay"


class Options:
    """The stream limits. They are the drop rule's numbers, so a test can make them small and prove the rule."""

    def __init__(self, *, heartbeat_s: float = HEARTBEAT_S, write_timeout_s: float = WRITE_TIMEOUT_S, queue_size: int = DEFAULT_QUEUE,
                 preview_first_frame_s: float = PREVIEW_FIRST_FRAME_S):
        self.heartbeat_s, self.write_timeout_s = heartbeat_s, write_timeout_s
        self.queue_size, self.preview_first_frame_s = queue_size, preview_first_frame_s


BRIDGE: web.AppKey = web.AppKey("kc_bridge", Bridge)
GUARD: web.AppKey = web.AppKey("kc_guard", Guard)
BUILD: web.AppKey = web.AppKey("kc_build", WebBuild)
SANITIZER: web.AppKey = web.AppKey("kc_sanitizer", OutgoingSanitizer)
STATE: web.AppKey = web.AppKey("kc_state", ApiState)
OPTIONS: web.AppKey = web.AppKey("kc_options", Options)
VLA: web.AppKey = web.AppKey("kc_vla", object)  # the VLA beat's forward (console_api/vla.py); absent unless --vla-endpoint


# ---- plumbing ---------------------------------------------------------------------------------------------------------------

def _sanitizer_of(request: web.Request) -> Callable:
    """The one scrub every body leaves through, unary or streamed."""
    sanitizer = request.app[SANITIZER]
    return lambda payload: sanitizer.scrub(payload, generic=True)[0]


def _json(request: web.Request, payload, *, status: int = 200, cache: Optional[str] = None) -> web.Response:
    clean = _sanitizer_of(request)(payload)
    body = json.dumps(clean, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return web.Response(body=body, status=status, headers={"Content-Type": "application/json", "Cache-Control": cache or "no-store"})


def _refused(request: web.Request, reason_code: str, status: int = 403) -> web.Response:
    return _json(request, {"ok": False, "reason_code": reason_code}, status=status)


def _forwarded(request: web.Request, answer, *, cache: Optional[str] = None) -> web.Response:
    """A runtime answer, as it is. A refusal keeps the runtime's reason_code and gets the status that matches it."""
    if isinstance(answer, dict) and answer.get("ok") is False:
        return _json(request, answer, status=STATUS_FOR_REASON.get(answer.get("reason_code"), 400))
    return _json(request, answer, cache=cache)


def _command_result(command_id: str, reason_code: str) -> dict:
    return {"command_id": command_id, "status": "rejected", "reason_code": reason_code, "run_id": None}


def _positive_int(value, default: int) -> Optional[int]:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _command_block(request: web.Request) -> Optional[str]:
    """Why this process refuses to command at all, before any command is looked at."""
    if request.app[BRIDGE].read_only:
        return "contract_incompatible"
    if request.app[STATE].replay_only:
        return "replay_only"
    return None


# ---- read models ------------------------------------------------------------------------------------------------------------

async def handle_hello(request: web.Request) -> web.Response:
    """The handshake. A tab that states another contract version is told to be READ-ONLY and is never told anything else.

    It also says WHO OWNS THE CREDENTIAL this page was served with. `credential_owner` is 'runtime_session' when it
    came from the runtime's own store and therefore survives a restart of this process, and 'api_process' when this
    process had to mint one because no runtime had named a session yet, in which case a restart does invalidate an open
    tab. A console that reads a 403 can compare `runtime_session` with the one it booted against and tell an expired
    credential from a refusal, instead of guessing which of the two it is looking at."""
    asked = request.query.get("contract_version")
    state, bridge = request.app[STATE], request.app[BRIDGE]
    if asked is not None and asked != CONTRACT_VERSION:
        return _json(request, {"ok": False, "reason_code": "contract_incompatible", "contract_version": CONTRACT_VERSION,
                               "read_only": True, "commands_enabled": False, "api": {"session": state.session, "version": API_VERSION}})
    runtime = {"reachable": False, "session": None, "contract_version": None, "data_mode": None}
    reason_code = None
    try:
        answer = await bridge.hello()
        runtime["reachable"] = True
        runtime["contract_version"] = answer.get("contract_version")
        runtime["session"] = answer.get("runtime_session")
        if answer.get("ok") is not True:
            reason_code = answer.get("reason_code") or "contract_incompatible"
        else:
            state.bind(answer.get("runtime_session"))
            snapshot = await bridge.snapshot()
            state.note(snapshot)
            runtime["data_mode"] = state.data_mode
    except RuntimeUnavailable:
        reason_code = "runtime_unavailable"
    read_only = reason_code is not None or bridge.read_only
    return _json(request, {"ok": reason_code is None, "reason_code": reason_code, "contract_version": CONTRACT_VERSION,
                           "read_only": read_only, "commands_enabled": not read_only and not state.replay_only,
                           "runtime_session": runtime["session"],  # at the top level too: it is what a page names a session by
                           "credential_owner": state.credential_owner, "bound_runtime_session": state.runtime_session,
                           "api": {"session": state.session, "version": API_VERSION}, "runtime": runtime})


async def handle_snapshot(request: web.Request) -> web.Response:
    """The runtime's snapshot, forwarded WHOLE.

    'Whole' is load-bearing and is pinned by a test, and it is now simply what forwarding the contract object means:
    'unresolved_command' and 'lease' are OPTIONAL PROPERTIES of snapshot.schema.json. Nothing here special-cases
    either of them, nothing strips a key and nothing repairs one. A snapshot from a runtime that sends neither is a
    valid snapshot saying there is no block and nothing to report about a lease.

    This used to be a workaround and the reason it existed is worth keeping written down. The two keys travelled
    BESIDE the contract object, because the snapshot refuses unknown keys and no round owned that schema file; this
    process had to be told not to strip them, and a client that validated had to strip them first. The behaviour that
    must not change now the workaround is gone is this one: without those keys the browser cannot explain a block
    without a terminal, which is the whole point of the panel that reads it.
    """
    try:
        snapshot = await request.app[BRIDGE].snapshot()
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    request.app[STATE].note(snapshot)
    return _forwarded(request, snapshot)


async def handle_cake_status(request: web.Request) -> web.Response:
    """The Cake resident status, forwarded whole.

    A READ, and only a read: it takes no body, carries no command id and reaches a runtime op whose whole job is to
    project a value that was already polled. A page load or a reconnect that fetches this submits nothing.

    It is deliberately NOT part of the snapshot. The snapshot is contract v1, whose `cake` object refuses unknown keys;
    this surface carries its own `cake_status_version` and can be revised without touching that contract. A console
    reads structured fields here instead of parsing the ladder's evidence prose, which is written for an operator to
    read and is not a data format.
    """
    try:
        return _forwarded(request, await request.app[BRIDGE].cake_status())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_robot_status(request: web.Request) -> web.Response:
    """The ROBOT's resident, forwarded whole: its own robot_status_version, never a contract object.

    A READ, and only a read: no body, no command id, and a runtime op that projects a reading it already polled. A
    page load or a reconnect that fetches this submits nothing and grants nothing."""
    try:
        return _forwarded(request, await request.app[BRIDGE].robot_status())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_post_robot_confirm_session(request: web.Request) -> web.Response:
    """The operator confirms the connected robot's resident session. It GRANTS permission to move, so it passes the SAME
    guard POST /api/commands passes, through the same function (_command_body -> Guard.check_command), then the same
    replay block and the same runtime-session check a command gets.

    THE BODY IS EXACTLY {"session_uuid": "<the session the operator saw>", "host_instance": {"slot_instance": "<decimal
    handle>", "spawn_sequence": N, "pid": N}}, the host instance exactly as GET /api/robot displayed it. Any other key, or
    a missing one, at either level refuses the whole body: there is no field for anything else to arrive in. The runtime
    then compares each component with a fresh read of the robot and refuses on any difference; that refusal does not
    carry the current values, which have to be read again from GET /api/robot."""
    body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    session_uuid = body.get("session_uuid")
    if set(body) != {"session_uuid", "host_instance"} or not isinstance(session_uuid, str) or not 0 < len(session_uuid) <= 64:
        return _refused(request, "bad_request", 400)
    host_instance = host_instance_of(body.get("host_instance"))
    if host_instance is None:
        return _refused(request, "bad_request", 400)
    blocked = _command_block(request)
    if blocked is not None:
        return _refused(request, blocked, 409)
    changed = await _verify_runtime_session(request)
    if changed is not None:
        return _refused(request, changed, STATUS_FOR_REASON.get(changed, 409))
    try:
        return _forwarded(request, await request.app[BRIDGE].robot_confirm_session(session_uuid, host_instance))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


async def handle_capability_check_state(request: web.Request) -> web.Response:
    """The capability slot check's READ, forwarded whole: its own capability_check_version, never a contract object.

    No body, no command id, and a runtime op that projects a reading it already polled plus its own record. A page load or
    a reconnect that fetches this submits nothing and claims nothing."""
    try:
        return _forwarded(request, await request.app[BRIDGE].capability_check_state())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_post_capability_check(request: web.Request) -> web.Response:
    """Apply ONE prepared Plan to the robot's capability slot and read it back. NO MOTION, and never for a mission.

    It moves a Cake slot, so it passes the SAME guard POST /api/commands passes, through the same function
    (_command_body -> Guard.check_command), then the same replay block and the same runtime-session check a command gets,
    exactly as POST /api/robot/confirm-session does.

    THE BODY IS EXACTLY {"candidate_key": "<a key from the runtime's own list>", "payload_digest": "sha256:<the digest
    shown beside that key>", "session_uuid": "<the robot resident session the operator was shown>"}. Any other key, or a
    missing one, refuses the whole body: there is no field for Plan bytes, a path, a socket or an admin operation to
    arrive in. The runtime then refuses, with nothing submitted, a key it did not prepare, a digest that is not that key's,
    a check already in flight, a mission or a run holding the slot, and a fresh read of the robot that does not report the
    pinned build, that session and a HELD host."""
    body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    if set(body) != {"candidate_key", "payload_digest", "session_uuid"}:
        return _refused(request, "bad_request", 400)
    key, digest, session = body.get("candidate_key"), body.get("payload_digest"), body.get("session_uuid")
    if not isinstance(key, str) or not 0 < len(key) <= 64 or not isinstance(digest, str) or _PAYLOAD_DIGEST.fullmatch(digest) is None \
            or not isinstance(session, str) or _RESIDENT_SESSION.fullmatch(session) is None:
        return _refused(request, "bad_request", 400)
    blocked = _command_block(request)
    if blocked is not None:
        return _refused(request, blocked, 409)
    changed = await _verify_runtime_session(request)
    if changed is not None:
        return _refused(request, changed, STATUS_FOR_REASON.get(changed, 409))
    try:
        return _forwarded(request, await request.app[BRIDGE].capability_check_apply(key, digest, session))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


async def handle_capability_signature_state(request: web.Request) -> web.Response:
    """The signature policy check's READ, forwarded whole: its own signature_check_version, never a contract object.

    No body, no command id, and a runtime op that projects its own record. A page load or a reconnect that fetches this
    runs nothing."""
    try:
        return _forwarded(request, await request.app[BRIDGE].capability_signature_state())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_post_capability_signature(request: web.Request) -> web.Response:
    """Run the release's own verifier on the robot over every deployed capsule. NO MOTION, no slot, never a mission.

    It starts child processes on the robot, so it passes the SAME guard POST /api/commands passes, through the same
    function (_command_body -> Guard.check_command), then the same replay block and the same runtime-session check a
    command gets, exactly as POST /api/capability/check does.

    THE BODY IS EXACTLY {}. Any key at all refuses the whole body: there is no field for a capsule, a key, a path or a
    policy to arrive in. The verifier, the store, the trusted keys and the policy are the deployed release's own. The
    runtime then refuses, with nothing run, a check already in flight, a mission or a run holding the slot, a slot check
    in flight, and a release it cannot read as one under require."""
    body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    if body != {}:
        return _refused(request, "bad_request", 400)
    blocked = _command_block(request)
    if blocked is not None:
        return _refused(request, blocked, 409)
    changed = await _verify_runtime_session(request)
    if changed is not None:
        return _refused(request, changed, STATUS_FOR_REASON.get(changed, 409))
    try:
        return _forwarded(request, await request.app[BRIDGE].capability_signature_check())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


async def handle_history(request: web.Request) -> web.Response:
    try:
        return _forwarded(request, await request.app[BRIDGE].history())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_history_events(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    if demo_contracts.validate("common#/$defs/id", run_id):
        return _refused(request, "bad_request", 400)
    try:
        return _forwarded(request, await request.app[BRIDGE].history_events(run_id))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_run_output(request: web.Request) -> web.Response:
    """The controller's own output for one run. Authorized, redacted and bounded exactly like every other read here.

    The RUN ID is the only thing a caller names. The runtime composes the path from it, so nothing in the request can
    point at a file: `?stream=` chooses between the two the supervisor writes and is refused if it is anything else.
    """
    run_id = request.match_info["run_id"]
    if demo_contracts.validate("common#/$defs/id", run_id):
        return _refused(request, "bad_request", 400)
    stream = request.query.get("stream", "stdout")
    if stream not in ("stdout", "stderr"):
        return _refused(request, "bad_request", 400)
    # `_positive_int` admits zero, which is a meaningless bound for a tail. It is refused here rather than sent on for
    # the runtime to refuse again. This is the floor; the runtime applies its own ceiling.
    asked = request.query.get("max_bytes")
    max_bytes = None
    if asked is not None:
        max_bytes = _positive_int(asked, None)
        if max_bytes is None or max_bytes == 0:
            return _refused(request, "bad_request", 400)
    try:
        return _forwarded(request, await request.app[BRIDGE].run_output(run_id, stream, max_bytes))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_observation(request: web.Request) -> web.Response:
    what = request.match_info["what"]
    if what not in OBSERVATION_KINDS:
        return _refused(request, "bad_request", 400)
    try:
        answer = await request.app[BRIDGE].observation(request.match_info["observation_id"], what)
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    return _forwarded(request, answer, cache=IMMUTABLE)  # a stored observation never changes


async def handle_frame(request: web.Request) -> web.Response:
    """The decision frame, transcoded by the runtime. The stored frame is evidence: it is read, never replaced."""
    what = request.query.get("what", "raw")
    width = _positive_int(request.query.get("w"), 640)
    if what not in FRAME_KINDS or width is None:
        return _refused(request, "bad_request", 400)
    try:
        answer = await request.app[BRIDGE].frame(request.match_info["observation_id"], what, width)
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    if not isinstance(answer, dict) or not isinstance(answer.get("bytes_b64"), str):
        return _forwarded(request, answer if isinstance(answer, dict) else {"ok": False, "reason_code": "not_found"})
    try:
        image = base64.b64decode(answer["bytes_b64"], validate=True)
    except (binascii.Error, ValueError):
        return _refused(request, "frame_undecodable", 502)
    return web.Response(body=image, headers={"Content-Type": answer.get("content_type") or "image/jpeg", "Cache-Control": IMMUTABLE})


# ---- streams ----------------------------------------------------------------------------------------------------------------

async def handle_events(request: web.Request) -> web.StreamResponse:
    """since comes from the query, or from Last-Event-ID when a browser reconnects by itself."""
    raw = request.query.get("since")
    if raw is None:
        raw = request.headers.get("Last-Event-ID")
    since = _positive_int(raw, 0)
    if since is None:
        return _refused(request, "bad_request", 400)
    options = request.app[OPTIONS]
    stream = request.app[BRIDGE].events(since, loop=asyncio.get_running_loop(), maxsize=options.queue_size)
    return await sse_pump(request, stream, render_event, sanitize=_sanitizer_of(request), heartbeat_s=options.heartbeat_s, write_timeout_s=options.write_timeout_s)


async def handle_telemetry(request: web.Request) -> web.StreamResponse:
    options = request.app[OPTIONS]
    stream = request.app[BRIDGE].telemetry(loop=asyncio.get_running_loop(), maxsize=options.queue_size)
    return await sse_pump(request, stream, telemetry_renderer(), sanitize=_sanitizer_of(request), heartbeat_s=options.heartbeat_s, write_timeout_s=options.write_timeout_s)


def _preview_record(line, role: str):
    """ONE camera's record out of a telemetry line, or None when this line is not about that camera.

    The runtime publishes a topic per role (`preview:wrist`, `preview:front`), so the role is read from the TOPIC and a
    reader asking for the front camera can never be handed the wrist picture. The older single-topic `preview` line is
    still accepted, and then the record's own `role` decides: a runtime that publishes one stream must not have its
    frames silently served under whichever role was asked for.
    """
    if not isinstance(line, dict):
        return None
    record = line.get(f"preview:{role}")
    if record is not None:
        return record
    record = line.get("preview")
    if record is None or (isinstance(record, dict) and record.get("role") not in (None, role)):
        return None
    return record


def _preview_bytes(record) -> Optional[bytes]:
    if not isinstance(record, dict) or not isinstance(record.get("bytes_b64"), str):
        return None
    try:
        return base64.b64decode(record["bytes_b64"], validate=True)
    except (binascii.Error, ValueError):
        return None


async def handle_preview(request: web.Request) -> web.StreamResponse:
    """A picture for a human, fed from the runtime's LATEST preview frame. No device is opened here and no frame is
    invented: when the runtime has no preview to give, this endpoint says so and sends no image at all."""
    role = request.query.get("role", "wrist")
    if role not in PREVIEW_ROLES:
        return _refused(request, "bad_request", 400)
    options = request.app[OPTIONS]
    stream = request.app[BRIDGE].telemetry(loop=asyncio.get_running_loop(), maxsize=options.queue_size)
    # `state` is the source's own word for why there is no picture (disconnected, connecting, stalled, failed): it is
    # carried through so a caller that reads this body gets the same three-way distinction the telemetry slot gives,
    # instead of one flat "unavailable".
    unavailable = {"ok": False, "reason_code": "preview_unavailable", "role": role, "available": False, "state": None}
    first = None
    deadline = asyncio.get_running_loop().time() + options.preview_first_frame_s
    try:
        while first is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                line = await asyncio.wait_for(stream.get(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                break
            if isinstance(line, StreamEnded):
                unavailable["reason_code"] = line.reason_code
                break
            record = _preview_record(line, role)
            if record is None:
                continue
            first = _preview_bytes(record)
            if first is None:
                unavailable["reason_code"] = record.get("reason") or "preview_unavailable"
                unavailable["available"] = bool(record.get("available"))
                state = record.get("state")
                unavailable["state"] = state if isinstance(state, str) else None
                break
        if first is None:
            stream.close()
            return _json(request, unavailable, status=503)
        return await _stream_preview(request, stream, role, first)
    finally:
        stream.close()


async def _stream_preview(request: web.Request, stream, role: str, first: bytes) -> web.StreamResponse:
    boundary = f"kcpreview{secrets.token_hex(8)}"
    response = web.StreamResponse(status=200, headers={"Content-Type": f"multipart/x-mixed-replace; boundary={boundary}", "Cache-Control": "no-store"})
    await response.prepare(request)
    limit_write_buffer(request)  # a stalled viewer holds no backlog of pictures either
    channel = Channel(request, response, request.app[OPTIONS].write_timeout_s)
    try:
        await _preview_part(channel, boundary, first)
        while True:
            line = await stream.get()
            if isinstance(line, StreamEnded):
                break
            record = _preview_record(line, role)
            if record is None:
                continue
            image = _preview_bytes(record)
            if image is None:
                break  # the source went away: the stream ends rather than repeating a stale picture
            await _preview_part(channel, boundary, image)
    except (SlowReader, ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
        pass
    return response


async def _preview_part(channel: Channel, boundary: str, image: bytes) -> None:
    head = f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(image)}\r\n\r\n".encode("ascii")
    await channel.send(head + image + b"\r\n")


# ---- commands ---------------------------------------------------------------------------------------------------------------

async def handle_command_result(request: web.Request) -> web.Response:
    command_id = request.match_info["command_id"]
    if demo_contracts.validate("common#/$defs/id", command_id):
        return _refused(request, "bad_request", 400)
    try:
        return _forwarded(request, await request.app[BRIDGE].result(command_id))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def _command_body(request: web.Request):
    """(body, refusal). The guard first, then a body that is at least a command with a usable command_id."""
    reason = request.app[GUARD].check_command(request)
    if reason is not None:
        return None, _refused(request, reason, 403)
    try:
        body = await request.json(loads=json.loads)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None, _refused(request, "body_not_json", 400)
    if not isinstance(body, dict):
        return None, _refused(request, "body_not_json", 400)
    return body, None


def _unwrap(body: dict):
    """(command, refusal_code). The body IS the contract command. A client that wraps it, {"command_id": id, "command":
    {...}}, is accepted too: the id it names is the idempotency key either way. The two ids must AGREE. A mismatch is
    refused rather than guessed at, because guessing would key the result on the wrong command."""
    inner = body.get("command")
    if not isinstance(inner, dict):
        return body, None
    command, outer = dict(inner), body.get("command_id")
    if isinstance(outer, str) and outer:
        if not isinstance(command.get("command_id"), str):
            command["command_id"] = outer
        elif command["command_id"] != outer:
            return None, "command_id_mismatch"
    return command, None


async def _verify_runtime_session(request: web.Request) -> Optional[str]:
    """Ask the runtime who it is BEFORE a command is forwarded to it.

    The credential this request presented belongs to a runtime session, and readiness was established against that
    session. A daemon that has been replaced since answers with a different one, and a command sent to it would be a
    command nobody has done readiness for. One handshake on the runtime's own socket is what this costs. It is not
    airtight, because the handshake and the command are two connections and a runtime could be replaced between them;
    it closes the window that lasts as long as this process has not noticed, which is the one that lasts minutes.

    A session it has never bound to is not checked: there is nothing to compare against and the request is answered by
    the runtime that is there."""
    state, bridge = request.app[STATE], request.app[BRIDGE]
    if state.runtime_session is None:
        return None
    try:
        answer = await bridge.hello()
    except RuntimeUnavailable:
        return "runtime_unavailable"
    session = answer.get("runtime_session") if isinstance(answer, dict) else None
    if not isinstance(session, str) or session == state.runtime_session:
        return None
    state.bind(session)  # follow it, so the next reload of the page gets the credential that runtime owns
    return "runtime_session_changed"


def _reused_id(request: web.Request, command_id: str, answer, submitted) -> Optional[web.Response]:
    """409 when the runtime's receipt is for a DIFFERENT body under this id, and nothing otherwise.

    The runtime answered out of its journal and started nothing, so this is a conflict to report rather than a command
    to worry about. A receipt with no digest says the journal cannot tell; that is left alone, because refusing on it
    would refuse every repeat of a command the runtime rejected."""
    if not isinstance(answer, dict):
        return None
    recorded = answer.get("payload_digest")
    if not isinstance(recorded, str) or submitted is None or recorded == submitted:
        return None
    return _json(request, {"ok": False, "reason_code": "command_id_reused", "command_id": command_id,
                           "recorded_payload_digest": recorded, "submitted_payload_digest": submitted,
                           "recorded_status": answer.get("status"), "recorded_run_id": answer.get("run_id")}, status=409)


async def handle_post_command(request: web.Request) -> web.Response:
    body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    body, mismatch = _unwrap(body)
    if mismatch is not None:
        return _refused(request, mismatch, 400)
    command_id = body.get("command_id")
    if not isinstance(command_id, str) or demo_contracts.validate("common#/$defs/id", command_id):
        return _refused(request, "command_invalid", 400)  # nothing to key a result on: there is no result to return
    client = body.get("client") if isinstance(body.get("client"), dict) else {}
    if body.get("contract_version") != CONTRACT_VERSION or client.get("contract_version") != CONTRACT_VERSION:
        return _json(request, _command_result(command_id, "contract_incompatible"))
    blocked = _command_block(request)
    if blocked is not None:
        return _json(request, _command_result(command_id, blocked))
    changed = await _verify_runtime_session(request)
    if changed is not None:
        return _refused(request, changed, STATUS_FOR_REASON.get(changed, 409))
    try:
        answer = await request.app[BRIDGE].submit(body)
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _json(request, _command_result(command_id, "contract_incompatible"))
    if not isinstance(answer, dict) or not isinstance(answer.get("status"), str):
        reason = answer.get("reason_code") if isinstance(answer, dict) else None
        return _json(request, _command_result(command_id, reason or "runtime_refused"))
    answer, _mission = _split_mission_part(answer)  # a stop submitted here is answered with its command_result alone
    conflict = _reused_id(request, command_id, answer, payload_digest(body))
    return conflict if conflict is not None else _json(request, answer)


def _split_mission_part(answer: dict) -> tuple:
    """(the command_result alone, the mission's part of a stop or None), from the runtime's answer to a submit.

    The runtime puts the mission's part BESIDE the fields of a stop command's command_result
    (demo_runtime.daemon._op_submit). Taking it off here is what keeps every command_result this process hands on
    exactly the contract object: a receipt with a key the schema does not have would fail a client that validates it.
    The runtime's answer is copied, never edited in place."""
    receipt = {key: value for key, value in answer.items() if key != "mission"}
    part = answer.get("mission")
    return receipt, (part if isinstance(part, dict) else None)


async def handle_post_resolve(request: web.Request) -> web.Response:
    """Put the uncertain command's question back to the runtime, with the operator's declared finding.

    This is NOT a submit. Nothing is queued, nothing is repeated and no command_id is minted: the id in the path names a
    command the runtime already holds. The body carries the operator's `finding` (one of the strings the runtime names
    in the snapshot's unresolved_command.findings) and an optional `note`. The runtime re-reads the process table, the
    run's own files and the observation store, classifies what it found, records the finding as an attestation beside
    that evidence and decides on its own whether the block lifts. The answer is forwarded whole, so the browser gets the
    machine evidence, the classification and whether the block cleared, and a refusal keeps the runtime's reason_code.
    """
    command_id = request.match_info["command_id"]
    body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    if demo_contracts.validate("common#/$defs/id", command_id):
        return _refused(request, "bad_request", 400)
    finding = body.get("finding")
    if not isinstance(finding, str) or finding == "":
        # Refused here, with the RUNTIME's own word for it, because there is nothing to forward: a finding that is not
        # a string is not a statement anybody made. The allowed values are the ones the snapshot already named.
        return _refused(request, "finding_not_allowed", 400)
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        return _refused(request, "note_not_allowed", 400)
    blocked = _command_block(request)
    if blocked is not None:
        return _refused(request, blocked, 409)
    try:
        return _forwarded(request, await request.app[BRIDGE].resolve(command_id, finding, note))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


# ---- the mission --------------------------------------------------------------------------------------------------------------
#
# ITS OWN SURFACE, NOT CONTRACT v1. A mission is not a command and a target is not a run: nothing here carries a
# contract object, adds a command type or widens the snapshot. The runtime's mission view carries its own
# mission_record_version, exactly as cake_status carries its own.
#
# THE TWO READS take no body and reach ops that only fold a file that was already written. A page load or a reload
# fetches them and starts nothing, submits nothing and resumes nothing.
#
# THE THREE POSTS PASS THE SAME GUARD POST /api/commands PASSES, through the same function: _command_body() runs
# Guard.check_command, which is the Host, Origin, Content-Type, X-Console-Request and X-Console-Credential check, in
# that order, with the runtime session's own credential. There is no weaker path to any of them.

async def handle_mission_state(request: web.Request) -> web.Response:
    """What the runtime has on the record for the mission. A read, and only a read."""
    try:
        return _forwarded(request, await request.app[BRIDGE].mission_state())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def handle_mission_candidates(request: web.Request) -> web.Response:
    """The activation candidates this runtime prepared, by key and payload digest.

    A KEY AND A DIGEST ARE THE WHOLE ANSWER. The browser picks a key from this list and sends that key back; it never
    sees, holds or sends the bytes, and no path appears here at all - which is also what the outgoing sanitizer would
    remove if one ever did.
    """
    try:
        return _forwarded(request, await request.app[BRIDGE].mission_candidates())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)


async def _mission_post(request: web.Request):
    """(body, refusal) for the four mission POSTs: the shared guard, then the replay block.

    It is _command_body() that does the guard work, unchanged and unwrapped, so these routes cannot drift away from
    the one POST /api/commands uses.
    """
    body, refusal = await _command_body(request)
    if refusal is not None:
        return None, refusal
    blocked = _command_block(request)
    if blocked is not None:
        return None, _refused(request, blocked, 409)
    return body, None


# ---- the VLA beat: forwards to the robot-side runner, decides nothing ------------------------------------------------------------

async def handle_vla_status(request: web.Request) -> web.Response:
    forward = request.app.get(VLA)
    if forward is None:
        return _refused(request, "vla_not_configured", 404)
    answer = await forward.call("status")
    if answer is None:
        return _json(request, {"state": "unreachable", "reason_code": "vla_runner_unreachable"})
    return _json(request, answer)


async def handle_post_vla(request: web.Request) -> web.Response:
    """swap, unswap, run, stop: the guard, then the runner's own answer. Nothing is decided here."""
    _body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    forward = request.app.get(VLA)
    if forward is None:
        return _refused(request, "vla_not_configured", 404)
    answer = await forward.call(request.match_info["op"])
    if answer is None:
        return _refused(request, "vla_runner_unreachable", 503)
    return _json(request, answer)


async def handle_post_mission_start(request: web.Request) -> web.Response:
    body, refusal = await _mission_post(request)
    if refusal is not None:
        return refusal
    targets = body.get("targets")
    if targets is not None and not isinstance(targets, list):
        return _refused(request, "bad_request", 400)
    try:
        return _forwarded(request, await request.app[BRIDGE].mission_start(targets))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


async def handle_post_mission_stop(request: web.Request) -> web.Response:
    """The mission stop. It takes no body beyond the guard's requirements and it is answered while a target is
    waiting on a capability, which is the state a mission spends most of this demonstration in."""
    _body, refusal = await _mission_post(request)
    if refusal is not None:
        return refusal
    try:
        return _forwarded(request, await request.app[BRIDGE].mission_stop())
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


async def handle_post_mission_activate(request: web.Request) -> web.Response:
    """Activate ONE prepared candidate, named by key.

    THE BODY IS A CANDIDATE KEY AND NOTHING ELSE, and that is enforced here rather than left to the runtime: a body
    carrying any other key is refused whole, so there is no field for Plan bytes, a file path, a socket path or an
    admin operation to arrive in, and no chance of one being accepted because it was ignored. The runtime then refuses
    a key it did not prepare, by name, without submitting anything.
    """
    body, refusal = await _mission_post(request)
    if refusal is not None:
        return refusal
    candidate_key = body.get("candidate_key")
    if set(body) != {"candidate_key"} or not isinstance(candidate_key, str) or not candidate_key:
        return _refused(request, "bad_request", 400)
    try:
        return _forwarded(request, await request.app[BRIDGE].mission_activate(candidate_key))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


async def handle_post_mission_retry(request: web.Request) -> web.Response:
    """Retry the attempt a mission HOLDS on for the operator's decision.

    THE BODY IS AT MOST THE MISSION ID THE OPERATOR SAW HELD, so a press meant for one mission never retries another. A
    body carrying any other key, or an id that is not a non-empty text, is refused whole; the runtime decides the target,
    the attempt and the park from the hold on its record, and refuses a hold that offers no retry.
    """
    body, refusal = await _mission_post(request)
    if refusal is not None:
        return refusal
    mission_id = body.get("mission_id")
    if set(body) - {"mission_id"} or ("mission_id" in body and (not isinstance(mission_id, str) or not mission_id)):
        return _refused(request, "bad_request", 400)
    try:
        return _forwarded(request, await request.app[BRIDGE].mission_retry(mission_id))
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)


def _stop_command(body) -> Optional[dict]:
    """The body as a contract stop COMMAND, or None when it is not one.

    The browser has always sent a whole contract command here and this route has always thrown it away. A body that
    names itself a stop and carries a usable command_id is submitted, which is what puts it in the journal under that
    id; anything else (an empty object from a command line client, a body of another type) keeps the plain stop op.
    Nothing is repaired: a body that almost looks like a command is not treated as one."""
    command, mismatch = _unwrap(body if isinstance(body, dict) else {})
    if mismatch is not None or not isinstance(command, dict) or command.get("type") != "stop":
        return None
    command_id = command.get("command_id")
    if not isinstance(command_id, str) or demo_contracts.validate("common#/$defs/id", command_id):
        return None
    return command if not demo_contracts.validate("command", command) else None


def _stop_answer(result: dict, mission: Optional[dict] = None) -> dict:
    """What a submitted stop tells a client, from the command_result the runtime journaled and the mission's part.

    FOUR FACTS, KEPT APART. `command` is the receipt: this id was admitted, once, and asking again by id answers the
    same. `ok` is whether the RUNTIME took the request. The stop object is what it took it against, and `mission` is
    the mission's own answer. Whether the robot stopped is none of them and is not claimed here: the run's
    stop.requested, stop.acknowledged and stop.outcome records say that.

    A STOP THAT ENDED A MISSION WAS TAKEN, RUN OR NO RUN. A mission waiting on a capability holds the run slot with no
    controller run under it, so the receipt says no_active_run while the mission part says stopping. Reporting that
    press as refused would tell the operator nothing was stopped when the mission was. So `ok` is true when a run was
    stopped OR the mission part says stopping, and the run's facts stay where they are true: the receipt keeps
    no_active_run and the stop object names no run. A press that stopped neither is refused exactly as before.

    requested_mono_ns is null on this path and that is deliberate. The runtime's plain stop op returns its own
    monotonic stamp; the journaled path returns a command_result, which has no field for one, and a number invented
    here would be this process's clock wearing the runtime's name. The stop.requested record carries the real one."""
    run_taken = result.get("status") == "finished" and result.get("reason_code") is None
    mission_taken = isinstance(mission, dict) and mission.get("stopping") is True
    taken = run_taken or mission_taken
    return {"ok": bool(taken), "reason_code": None if taken else result.get("reason_code"), "command": result,
            "stop": {"requested_mono_ns": None, "run_id": result.get("run_id") if run_taken else None} if taken else None,
            "mission": mission}


async def handle_post_stop(request: web.Request) -> web.Response:
    """The stop, on its own worker and without the session handshake a command gets.

    Both omissions are deliberate. A stop must reach the runtime's socket in the time it takes to connect to it, and a
    handshake first would put a round trip in front of the one request that is pressed because something is wrong. And
    a stop aimed at whatever runtime is there now is the right stop: the case a session check protects a START from,
    commanding a runtime nothing did readiness against, has no bad version for stopping one. The credential check still
    runs, and it is what a page holding a superseded value is refused by."""
    body, refusal = await _command_body(request)
    if refusal is not None:
        return refusal
    blocked = _command_block(request)
    if blocked is not None:
        return _refused(request, blocked, 409)
    command = _stop_command(body)
    try:
        if command is None:
            return _forwarded(request, await request.app[BRIDGE].stop())
        answer = await request.app[BRIDGE].submit_stop(command)
    except RuntimeUnavailable:
        return _refused(request, "runtime_unavailable", 503)
    except ReadOnlyClient:
        return _refused(request, "contract_incompatible", 409)
    if not isinstance(answer, dict) or not isinstance(answer.get("status"), str):
        reason = answer.get("reason_code") if isinstance(answer, dict) else None
        return _forwarded(request, {"ok": False, "reason_code": reason or "runtime_refused"})
    answer, mission = _split_mission_part(answer)
    conflict = _reused_id(request, command["command_id"], answer, payload_digest(command))
    return conflict if conflict is not None else _json(request, _stop_answer(answer, mission))


# ---- the built page ---------------------------------------------------------------------------------------------------------

async def _follow_session_before_serving(request: web.Request) -> None:
    """Ask the runtime who it is, and follow its session, BEFORE a page is handed a credential (redesign r4 verify-r2
    must_fix 1).

    A runtime replaced while no request reached this process leaves the credential of the session before it in hand. A
    page served that value and then made its own handshake, which is what makes this process follow the new session and
    rotate: the page held a credential superseded by its own first request, drew a working Stop, and had its first press,
    Stop included, refused. Reading the session here makes the credential served the one that stays accepted.

    A runtime that does not answer, answers with a refusal or does not answer in time changes nothing: the page is served
    what is in hand, exactly as before, and the boot block names the session it belongs to."""
    state, bridge = request.app[STATE], request.app[BRIDGE]
    try:
        answer = await asyncio.wait_for(bridge.hello(), timeout=INDEX_SESSION_WAIT_S)
    except (RuntimeUnavailable, ReadOnlyClient, asyncio.TimeoutError, OSError, ValueError):
        return
    if isinstance(answer, dict) and answer.get("ok") is True:
        state.bind(answer.get("runtime_session"))


async def handle_index(request: web.Request) -> web.Response:
    build, guard, state = request.app[BUILD], request.app[GUARD], request.app[STATE]
    if not build.attached:
        return _refused(request, "web_build_not_attached", 404)
    await _follow_session_before_serving(request)
    # The credential and the session it belongs to are read together, with nothing awaited between them. The session is
    # what lets the page tell, at its own handshake, that the credential it holds is no longer the one this process serves.
    boot = {"contract_version": CONTRACT_VERSION, "api_base": "/api", "api_session": state.session,
            "credential": guard.credential, "credential_header": "X-Console-Credential", "request_header": "X-Console-Request",
            "bound_runtime_session": state.runtime_session, "credential_owner": state.credential_owner}
    html = build.index_html(boot)
    if html is None:
        return _refused(request, "web_build_unreadable", 500)
    return web.Response(text=html, headers={"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                                            "Content-Security-Policy": CONTENT_SECURITY_POLICY})


async def handle_asset(request: web.Request) -> web.StreamResponse:
    from .web import asset_response

    relative = request.match_info.get("path", "")
    if not relative or relative == "index.html":
        return await handle_index(request)
    path = request.app[BUILD].locate(relative)
    if path is None:
        return _refused(request, "not_found", 404)
    return asset_response(path)


# ---- the application --------------------------------------------------------------------------------------------------------

def _harden(response: web.StreamResponse) -> None:
    if getattr(response, "prepared", False):
        return
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")


def make_middleware(guard: Guard):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if guard.check_host(request) is not None:
            return _refused(request, "host_not_allowed", 403)
        try:
            response = await handler(request)
        except web.HTTPException as exception:
            response = _refused(request, REASON_FOR_STATUS.get(exception.status, "request_refused"), exception.status)
        _harden(response)
        return response

    return middleware


def build_app(*, client, credential: str, web_build: Optional[str] = None, host: str = LOOPBACK, port: int = 0,
              private_tokens=(), options: Optional[Options] = None, credential_store: Optional[CredentialStore] = None,
              runtime_session: Optional[str] = None, vla=None) -> web.Application:
    """The application. client is a RuntimeClient or anything with its surface; nothing else reaches the runtime.

    credential_store and runtime_session are what make the credential outlive this process: with both, `credential` is
    the value that runtime session already owns and a restart of this process serves the same one. Without them the
    credential is this launch's own, exactly as it was, and an open tab must be reloaded after a restart."""
    guard = Guard(credential, host=host, port=port)
    tokens = [credential, str(repository_root()), *[str(token) for token in private_tokens if token]]
    app = web.Application(middlewares=[make_middleware(guard)])
    bridge = Bridge(client, contract_version=CONTRACT_VERSION)
    sanitizer = OutgoingSanitizer.for_runtime(None, extra=tokens)
    app[BRIDGE] = bridge
    app[GUARD] = guard
    app[BUILD] = WebBuild(web_build)
    app[SANITIZER] = sanitizer
    app[STATE] = ApiState(guard, store=credential_store, runtime_session=runtime_session, sanitizer=sanitizer)
    app[OPTIONS] = options or Options()
    if vla is not None:
        app[VLA] = vla

    async def close_bridge(_app) -> None:
        bridge.close()  # the dedicated stop worker; nothing else holds a thread of its own

    app.on_cleanup.append(close_bridge)
    app.router.add_get("/api/hello", handle_hello)
    app.router.add_get("/api/snapshot", handle_snapshot)
    app.router.add_get("/api/cake", handle_cake_status)
    app.router.add_get("/api/robot", handle_robot_status)
    app.router.add_post("/api/robot/confirm-session", handle_post_robot_confirm_session)
    app.router.add_get("/api/capability/check", handle_capability_check_state)
    app.router.add_post("/api/capability/check", handle_post_capability_check)
    app.router.add_get("/api/capability/signature", handle_capability_signature_state)
    app.router.add_post("/api/capability/signature", handle_post_capability_signature)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/telemetry", handle_telemetry)
    app.router.add_get("/api/preview.mjpg", handle_preview)
    app.router.add_get("/api/history", handle_history)
    app.router.add_get("/api/history/{run_id}/events", handle_history_events)
    app.router.add_get("/api/history/{run_id}/output", handle_run_output)
    app.router.add_get("/api/observations/{observation_id}/frame", handle_frame)  # before the {what} route
    app.router.add_get("/api/observations/{observation_id}/{what}", handle_observation)
    app.router.add_post("/api/commands", handle_post_command)
    app.router.add_get("/api/commands/{command_id}", handle_command_result)
    app.router.add_post("/api/commands/{command_id}/resolve", handle_post_resolve)
    app.router.add_post("/api/stop", handle_post_stop)
    # The mission, registered here and NOT after the catch-all below, which would swallow every one of them.
    app.router.add_get("/api/mission", handle_mission_state)
    app.router.add_get("/api/mission/candidates", handle_mission_candidates)
    app.router.add_post("/api/mission/start", handle_post_mission_start)
    app.router.add_post("/api/mission/stop", handle_post_mission_stop)
    app.router.add_post("/api/mission/activate", handle_post_mission_activate)
    app.router.add_post("/api/mission/retry", handle_post_mission_retry)
    # The VLA beat, before the catch-all like the mission routes: a forward to the robot-side runner, nothing more.
    app.router.add_get("/api/vla/status", handle_vla_status)
    app.router.add_post("/api/vla/{op:swap|unswap|run|stop|to_mission_pose|carry}", handle_post_vla)
    app.router.add_get("/", handle_index)
    app.router.add_get("/{path:.*}", handle_asset)
    return app
