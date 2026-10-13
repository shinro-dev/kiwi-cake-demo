# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.cake_activation: the ONE module of this runtime that can move a Cake slot.

It is named for what it does. `cake_admin` beside it answers questions and cannot mutate; everything that can is here,
so a reader auditing whether this console can activate a module reads one file.

WHAT IT SUBMITS, AND THE ONLY THING IT SUBMITS. `ApplyPlan` carrying a candidate Plan this process prepared, through
the shipped `admin-probe`, which is the supported client for the admin protocol. It never opens the admin socket
itself and never speaks the wire format: that protocol belongs to Cake. The one other request here that carries the
prepared bytes is a QUESTION, `ValidatePlan` (validate_plan): the resident answers it before its mutation branch and
changes nothing, and it sits beside the apply so every path that hands candidate bytes to the tool is in this file.

MEASURED, NOT ASSUMED. `ReplaceSlot` naming the capability slot and the mobile capsule is answered
`transaction_phase ABORTED` against this deployment and leaves the slot exactly as it was. The prepared candidate Plan
moves the capability slot and the holder slot TOGETHER, which is why one operation reaches it and a single-slot
replacement does not. Nothing is uploaded: both capsules were admitted when the resident started, and `AdmitCapsule`
is answered unavailable, so an activation chooses between builds the resident already holds. Diagnostics must never
describe a refusal here as an upload, a signature failure or a corrupted binary.

THE CANDIDATE IS SERVER-PREPARED. A caller names a candidate by a key this runtime already holds; it never supplies
Plan bytes, a file path, a socket path or an admin operation. Browser input selects among prepared candidates and
supplies nothing that reaches the tool's argument list. The prepared bytes are read once, hashed, and bound to the
operation uuid for the life of that operation.

THE OPERATION IDENTITY RULE, which the resident enforces and this encodes so a caller cannot get it wrong:
  - a NEW intended operation gets a FRESH uuid;
  - a RETRY reuses the SAME uuid and MUST carry byte-identical payload, which means the same prepared Plan bytes;
  - the same uuid with different bytes is refused by the resident and is a caller defect, reported as such;
  - after a reconnect the operation's STATE is retrieved by re-reading the slot, never resubmitted on a guess.

FOUR OUTCOMES THAT ARE NOT THE SAME THING, and this module never collapses them:
  - the REQUEST failed. The tool could not run, timed out, or the answer carried no transaction. Cake judged nothing.
  - the resident REFUSED the candidate BEFORE ANY TRANSACTION (`status mutation_refused` with its refusal class). That
    is Cake judging it: the bytes did not read as a Plan artifact whose digests hold (an INTEGRITY refusal, never a
    signature one), or the request's shape, the closure or the store refused it. No transaction was opened.
  - the TRANSACTION reached a phase. COMPLETED, ABORTED, or still running. `status ok` DOES NOT MEAN THE UPDATE
    HAPPENED: the dispatcher answers it for a transaction record without consulting the phase, so an ABORTED
    transaction arrives under `status ok`.
  - the ACTIVATION was OBSERVED. The capability slot, read back, carries the active capsule. This is the only one a
    mission may act on, and it is a separate read.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import uuid as uuid_module

from . import cake_admin

APPLY_PLAN = "apply-plan"
DEFAULT_TIMEOUT_S = 30.0

#: What an activation answer means, kept apart from how the request itself went. These names match
#: autonomy/motion/cake_capability_adapter.py, which is where the same vocabulary is defined for the offline reader.
PENDING, ACCEPTED, REJECTED_BY_CAKE, REQUEST_FAILED = "PENDING", "ACCEPTED", "REJECTED_BY_CAKE", "REQUEST_FAILED"
#: The resident answered `status mutation_refused` with a refusal class: it judged the candidate and opened no
#: transaction. Not a request that failed to land (REQUEST_FAILED), and not a transaction that aborted (REJECTED_BY_CAKE).
REFUSED_BEFORE_TRANSACTION = "REFUSED_BEFORE_TRANSACTION"
#: The status word admin-probe prints for that answer (cake-admin's response_status_reason).
MUTATION_REFUSED_STATUS = "mutation_refused"

#: The refusal classes a `refusal_code` carries, as the resident numbers them: 1 to 5 are the admin surface's own
#: payload-shape refusals (cake-admin MutationRefusal), 100 to 106 the runtime's (cake-runtime AdminRefusal, raised
#: before any transaction exists), and 110 plus a class a refused rollback. Each is (token, kind, words). The token is
#: the resident's own spelling; the words are this console's, and they claim no more than the class says.
REFUSAL_CLASSES = {
    1: ("payload_length_mismatch", "request_shape", "the request's payload is not the length its operation fixes"),
    2: ("payload_missing", "request_shape", "the request carried no payload where its operation requires one"),
    3: ("unknown_payload_mode", "request_shape", "a payload byte named a mode this resident does not know"),
    4: ("not_a_round_1_mutation", "request_shape", "the operation is not one this resident accepts as a mutation"),
    5: ("precondition_required", "request_shape", "the operation requires a precondition and the request carried none"),
    100: ("plan-unreadable", "integrity",
          "INTEGRITY: the candidate's bytes do not read as a Plan artifact whose digests hold, so no transaction was "
          "opened"),
    101: ("closure-unresolvable", "plan", "the active or the candidate Plan does not resolve, so no transaction was opened"),
    102: ("closure-did-not-converge", "plan", "the closure of the change did not converge, so no transaction was opened"),
    103: ("slot-not-in-plan", "plan", "the operation named a slot the active Plan does not hold"),
    104: ("plan-not-compilable", "plan", "the candidate does not compile to canonical bytes, so no transaction was opened"),
    105: ("journal-unavailable", "resident", "the resident could not open its journal, so no transaction was opened"),
    106: ("store-unavailable", "resident", "the resident could not open its Plan store, so no transaction was opened"),
}
#: A refused rollback is 110 plus the rollback's own class (1 to 7).
ROLLBACK_CLASS_BASE = 110


def refusal_label(code) -> dict:
    """What a refusal class means, for a record and a screen: {code, token, kind, words, rollback_class}.

    A code this console does not name is still a refusal before any transaction, said to be unnamed (kind
    `unclassified`, token None) rather than guessed at."""
    text = _decimal_or_none(code)
    number = int(text) if text is not None else None
    out = {"code": text, "token": None, "kind": "unclassified", "rollback_class": None,
           "words": "the resident refused the candidate before opening any transaction, with a class this console does "
                    "not name"}
    if number is None:
        return out
    if number in REFUSAL_CLASSES:
        token, kind, words = REFUSAL_CLASSES[number]
        out.update({"token": token, "kind": kind, "words": words})
    elif ROLLBACK_CLASS_BASE < number <= ROLLBACK_CLASS_BASE + 7:
        out.update({"token": "rollback-refused", "kind": "rollback", "rollback_class": number - ROLLBACK_CLASS_BASE,
                    "words": f"the resident refused a rollback (class {number - ROLLBACK_CLASS_BASE}) before opening any "
                             "transaction"})
    return out

#: What an ABORTED transaction is known to mean, and no more. The answer carries no cause, and none is supplied for
#: it: the handoff (section 8) recorded an admitted candidate completing on a fresh resident and aborting once the
#: resident had been up longer than 30 s, so "not a capsule this resident admitted" could be false. Nor is it an
#: upload, a signature or corruption.
REJECTION_DETAIL = "the resident answered ABORTED; the transaction did not complete, and the answer does not say why"

#: The lifecycle word the resident reports for a slot that is running its implementation. It is one half of what makes
#: a capability available; the capsule identity is the other, and neither decides alone. See capability_is_active.
ACTIVE = "ACTIVE"


def new_operation_uuid() -> str:
    """A fresh identity for a NEW intended operation. A retry does not call this; it reuses the recorded one."""
    return uuid_module.uuid4().hex


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


#: What each prepared candidate is FOR, named by the configuration, with the activation state its apply is expected to
#: record. A screen labels one button per candidate by its role and never preselects one; the primary action is the
#: `activate` role. The expected states are the ones this runtime records (mission.ACTIVATION_* and
#: REFUSED_BEFORE_TRANSACTION), spelled here because this module does not import the mission record:
#:   activate       the swapped Plan: the slot changes and, read back, carries the capability (OBSERVED);
#:   refused-demo   the abort-swap successor: the resident opens the transaction and ABORTS it, the slot unchanged;
#:   tampered-demo  the swapped Plan with one byte flipped: refused before any transaction, an INTEGRITY refusal;
#:   undo           the starting Plan: the transaction completes and the slot returns to the inert capsule, which reads
#:                  as completed without the capability.
ROLE_EXPECT = {"activate": "OBSERVED", "refused-demo": "REJECTED_BY_CAKE", "tampered-demo": REFUSED_BEFORE_TRANSACTION,
               "undo": "COMPLETED_NOT_OBSERVED"}
ROLE_LABELS = {"activate": "Activate the capability (the swapped Plan)",
               "refused-demo": "Abort-swap: Cake opens the transaction and aborts it, the slot unchanged",
               "tampered-demo": "Tampered twin: one byte flipped, refused by an integrity check before any transaction",
               "undo": "Undo: back to the starting Plan"}
MAX_ROLE_LABEL = 80


def roles_refusal(roles, keys) -> "str | None":
    """None when a role table is acceptable for these configured candidate keys, else the FIRST reason it is not:
    not a table, a key that is not a configured candidate, a role this runtime does not name, a role given twice, or a
    label that is not a short text. A configuration loader calls this; PreparedCandidates raises it."""
    if not isinstance(roles, dict):
        return "candidate_roles_not_a_table"
    seen = set()
    for key, entry in roles.items():
        if key not in keys:
            return "candidate_role_key_not_prepared"
        role, label = (entry.get("role"), entry.get("label")) if isinstance(entry, dict) else (entry, None)
        if role not in ROLE_EXPECT:
            return "candidate_role_unknown"
        if role in seen:
            return "candidate_role_repeated"
        seen.add(role)
        if label is not None and (not isinstance(label, str) or not 0 < len(label) <= MAX_ROLE_LABEL):
            return "candidate_role_label_not_allowed"
    return None


class PreparedCandidates:
    """The candidates this runtime is willing to activate, named by key, with their bytes read from disk once.

    A caller selects a KEY. It never supplies bytes and never supplies a path, so no browser input reaches the
    filesystem or the tool's argument list. A key that was not prepared is refused by name.

    `roles`, when given, says what each key is FOR (ROLE_EXPECT). It changes what the view shows and nothing that is
    submitted: a role is a label and an expectation, never a permission. A malformed table raises ValueError with the
    reason roles_refusal gives.
    """

    def __init__(self, candidates: dict, roles=None):
        if roles is not None:
            refused = roles_refusal(roles, list((candidates or {}).keys()))
            if refused is not None:
                raise ValueError(refused)
        self._roles: dict = {}
        for key, entry in (roles or {}).items():
            role, label = (entry.get("role"), entry.get("label")) if isinstance(entry, dict) else (entry, None)
            self._roles[key] = {"role": role, "label": label or ROLE_LABELS[role], "expect": ROLE_EXPECT[role]}
        self._has_roles = roles is not None
        self._bytes: dict = {}
        self._digests: dict = {}
        self._unreadable: dict = {}
        for key, path in sorted((candidates or {}).items()):
            if not isinstance(key, str) or not isinstance(path, str) or not os.path.isabs(path):
                self._unreadable[key] = "candidate_path_not_absolute"
                continue
            try:
                with open(path, "rb") as handle:
                    payload = handle.read()
            except OSError:
                # The reason names no path: a path is private and this answer is shown.
                self._unreadable[key] = "candidate_unreadable"
                continue
            if not payload:
                self._unreadable[key] = "candidate_empty"
                continue
            self._bytes[key] = payload
            self._digests[key] = digest_of(payload)

    def keys(self) -> list:
        return sorted(self._bytes)

    def digest(self, key: str):
        return self._digests.get(key)

    def payload(self, key: str):
        return self._bytes.get(key)

    def problem(self, key: str):
        return self._unreadable.get(key)

    def role(self, key: str):
        return (self._roles.get(key) or {}).get("role")

    def primary(self):
        """The key of the `activate` role, or None. Nothing is guessed: a runtime with no roles has no primary."""
        return next((key for key in self.keys() if self.role(key) == "activate"), None)

    def view(self) -> list:
        """What a console may see about the prepared candidates: their key and their digest, never their bytes or path.
        With roles configured, each entry also carries its role, its label and the state its apply is expected to record
        (None for a key the table does not name); without them the entry is exactly key and digest."""
        if not self._has_roles:
            return [{"key": key, "payload_digest": self._digests[key]} for key in self.keys()]
        out = []
        for key in self.keys():
            named = self._roles.get(key) or {}
            out.append({"key": key, "payload_digest": self._digests[key], "role": named.get("role"),
                        "label": named.get("label") or key, "expect": named.get("expect")})
        return out


def parse_activation_answer(text: str) -> dict:
    """What an activation answer says, with the request's own failure kept apart from the transaction's.

    This mirrors autonomy/motion/cake_capability_adapter.parse_activation_answer. It is duplicated rather than imported
    because `demo_runtime` is a packaged runtime that does not import `autonomy`, and a runtime that silently depended
    on a checkout layout would break in a pinned bundle. A test keeps the two readings equal.
    """
    fields = {}
    for raw in text.splitlines():
        name, separator, value = raw.strip().partition(" ")
        if separator:
            fields[name] = value.strip()
    status = fields.get("status")
    phase = fields.get("transaction_phase")
    out = {"status": status, "transaction_phase": phase, "transaction_uuid": fields.get("transaction_uuid"),
           "reason_code": fields.get("refusal_code"),
           # Carried for the record and never read for the outcome: the handle the resident gave the transaction, and
           # the epoch its answer printed when the answer carried a snapshot. None when the answer printed neither,
           # which is an answer that did not say, not an epoch of zero.
           "transaction_handle": _decimal_or_none(fields.get("transaction_handle")),
           "epoch": _decimal_or_none(fields.get("epoch"))}
    if status == MUTATION_REFUSED_STATUS and phase is None and _decimal_or_none(fields.get("refusal_code")) is not None:
        # CAKE JUDGED THE CANDIDATE AND OPENED NO TRANSACTION. The class says why; a refusal that names no class is not
        # read as a judgement and falls through to a request that did not land.
        label = refusal_label(fields.get("refusal_code"))
        out["outcome"] = REFUSED_BEFORE_TRANSACTION
        out["refusal"] = label
        out["detail"] = (f"the resident refused the candidate before any transaction (refusal class {label['code']}"
                         f"{', ' + label['token'] if label['token'] else ''}): {label['words']}")
        return out
    if status != "ok":
        out["outcome"] = REQUEST_FAILED
        out["detail"] = status or "no_status"
        return out
    if phase is None:
        out["outcome"] = REQUEST_FAILED
        out["detail"] = "the answer carried no transaction phase"
        return out
    if phase == "COMPLETED":
        out["outcome"] = ACCEPTED
        out["detail"] = "the transaction completed"
    elif phase == "ABORTED":
        out["outcome"] = REJECTED_BY_CAKE
        out["detail"] = REJECTION_DETAIL
    else:
        out["outcome"] = PENDING
        out["detail"] = phase
    return out


def submit_apply_plan(probe_path: str, socket_path: str, operation_uuid: str, payload: bytes,
                      *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """Submit ONE ApplyPlan under one operation identity, carrying exactly these bytes.

    The bytes are written to a private temporary file because that is what the tool takes, and the file is removed
    before this returns. A retry passes the SAME uuid and the SAME bytes and therefore writes the same file content,
    which is what makes it a retry rather than a new operation the resident will refuse.

    Never raises. A failure is a dict whose outcome is REQUEST_FAILED, which is NOT Cake judging the candidate.
    """
    if not probe_path or not os.path.isabs(probe_path) or not os.access(probe_path, os.X_OK):
        return {"outcome": REQUEST_FAILED, "detail": "probe_binary_unavailable", "status": None,
                "transaction_phase": None, "transaction_uuid": None, "reason_code": "probe_binary_unavailable"}
    if not socket_path or not os.path.exists(socket_path):
        return {"outcome": REQUEST_FAILED, "detail": "admin_socket_absent", "status": None,
                "transaction_phase": None, "transaction_uuid": None, "reason_code": "admin_socket_absent"}
    if not payload:
        return {"outcome": REQUEST_FAILED, "detail": "candidate_empty", "status": None,
                "transaction_phase": None, "transaction_uuid": None, "reason_code": "candidate_empty"}
    handle_fd, blob = tempfile.mkstemp(prefix="cake-candidate-", suffix=".cakepln")
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
        try:
            finished = subprocess.run(
                [probe_path, "request", socket_path, APPLY_PLAN, "--payload", blob, "--uuid", operation_uuid],
                capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL, check=False,
            )
        except subprocess.TimeoutExpired:
            # THE OPERATION MAY STILL BE RUNNING. This says the answer did not arrive, never that nothing happened,
            # and the caller confirms by re-reading the slot rather than by resubmitting on a guess.
            return {"outcome": REQUEST_FAILED, "detail": "admin_probe_timeout", "status": None,
                    "transaction_phase": None, "transaction_uuid": None, "reason_code": "admin_probe_timeout"}
        except OSError:
            return {"outcome": REQUEST_FAILED, "detail": "admin_probe_unrunnable", "status": None,
                    "transaction_phase": None, "transaction_uuid": None, "reason_code": "admin_probe_unrunnable"}
    finally:
        try:
            os.unlink(blob)
        except OSError:
            pass
    answer = parse_activation_answer(finished.stdout)
    if answer["outcome"] == REQUEST_FAILED and finished.returncode != 0 and not answer.get("reason_code"):
        answer["reason_code"] = "admin_probe_refused"
    return answer


# ---- ValidatePlan: a QUESTION about the exact prepared bytes, asked before an apply ----------------------------------
#
# It lives here, beside the one mutation, because it is the one read that carries candidate bytes: cake_admin's reads
# take no payload, and keeping every path that hands prepared Plan bytes to the tool in one file is what lets a reader
# audit them in one place. It changes nothing: the resident answers it before its mutation branch, takes no operation
# identity for it and writes no idempotency record (cake-admin dispatch, ValidatePlan). So no `--uuid` is passed.

VALIDATE_PLAN = "validate-plan"
#: What a ValidatePlan answer came to. `unanswered` is a request that produced no verdict (the tool could not run, the
#: socket was down, the resident answered without one); `not_asked` is a check with no validator configured.
PLAN_VALID, PLAN_INVALID, PLAN_UNANSWERED, PLAN_NOT_ASKED = "valid", "invalid", "unanswered", "not_asked"


def parse_validation_answer(text: str) -> dict:
    """admin-probe's ValidatePlan lines: `plan_verdict valid|invalid`, `plan_reason_code N`, and for a valid one
    `plan_artifact_digest <hex>`, under `status ok` (valid) or `status plan_invalid` (invalid). Anything else is not a
    verdict and is answered `unanswered` with the status it carried."""
    fields = {}
    for raw in (text if isinstance(text, str) else "").splitlines():
        name, separator, value = raw.strip().partition(" ")
        if separator:
            fields[name] = value.strip()
    status, verdict = fields.get("status"), fields.get("plan_verdict")
    out = {"verdict": PLAN_UNANSWERED, "status": _token_or_none(status, _WORD_TOKEN), "plan_reason_code": None,
           "plan_artifact_digest": None, "reason_code": None}
    if verdict == "valid" and status == "ok":
        out.update({"verdict": PLAN_VALID, "plan_reason_code": _decimal_or_none(fields.get("plan_reason_code")),
                    "plan_artifact_digest": _token_or_none(fields.get("plan_artifact_digest"), _HEX_TOKEN)})
    elif verdict == "invalid" and status == "plan_invalid":
        out.update({"verdict": PLAN_INVALID, "plan_reason_code": _decimal_or_none(fields.get("plan_reason_code"))})
    else:
        out["reason_code"] = out["status"] or "no_status"
    return out


def _unanswered(reason: str) -> dict:
    return {"verdict": PLAN_UNANSWERED, "status": None, "plan_reason_code": None, "plan_artifact_digest": None,
            "reason_code": reason}


def validate_plan(probe_path: str, socket_path: str, payload: bytes, *, timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S) -> dict:
    """Ask the resident whether THESE bytes are a Plan it would accept, without applying them. Never raises.

    The bytes go to a private temporary file, as for an apply, and the file is removed before this returns."""
    if not probe_path or not os.path.isabs(probe_path) or not os.access(probe_path, os.X_OK):
        return _unanswered("probe_binary_unavailable")
    if not socket_path or not os.path.exists(socket_path):
        return _unanswered("admin_socket_absent")
    if not payload:
        return _unanswered("candidate_empty")
    handle_fd, blob = tempfile.mkstemp(prefix="cake-validate-", suffix=".cakepln")
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
        try:
            finished = subprocess.run([probe_path, "request", socket_path, VALIDATE_PLAN, "--payload", blob],
                                      capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL,
                                      check=False)
        except subprocess.TimeoutExpired:
            return _unanswered("admin_probe_timeout")
        except OSError:
            return _unanswered("admin_probe_unrunnable")
    finally:
        try:
            os.unlink(blob)
        except OSError:
            pass
    return parse_validation_answer(finished.stdout)


def plan_validator(probe_path: str, socket_path: str, *, timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S):
    """validate_plan bound to one tool and one socket, the shape capability_check.CapabilityCheck takes as `validate`."""
    return lambda payload: validate_plan(probe_path, socket_path, payload, timeout_s=timeout_s)


# ---- the transaction's state path, rebuilt from the resident's flight ring -------------------------------------------
#
# A transaction's answer names one phase. The resident's flight ring holds every transition it took: event 0x03000006
# (cake-runtime EVT_TXN_TRANSITION) with arg0 the state left and arg1 the state entered, each numbered by its position
# in Specification 13.1's table, and a refused transition as event 0x03000007. A transition's correlation is the first
# eight bytes of the transaction uuid read little-endian (cake-runtime transaction::correlation_of). The ring holds a
# bounded number of records and every admin request writes one, so it is read right after the answer, and a path whose
# first record is not CREATED is said to have lost its start.

EVT_TXN_TRANSITION, EVT_TXN_TRANSITION_REFUSED = 0x03000006, 0x03000007
TX_STATES = ("CREATED", "VALIDATED", "PREPARED", "QUIESCING", "ACTIVATING", "COMMITTING", "COMMITTED", "DURABLE",
             "RETIRING", "COMPLETED", "ABORTED", "FAILED")
TX_TERMINAL = ("COMPLETED", "ABORTED", "FAILED")
#: How a path read went, beside READ_OK and READ_FAILED: no transaction to look for (a refusal before any, a failed
#: request), the ring answered and holds none of its records, or no flight reader was configured.
PATH_NO_TRANSACTION, PATH_NOT_IN_RING, PATH_NOT_ASKED = "no_transaction", "not_in_ring", "not_asked"
_UUID = re.compile(r"[0-9a-f]{32}")


def _state_name(code: int) -> str:
    return TX_STATES[code - 1] if 1 <= code <= len(TX_STATES) else str(code)


def _record_fields(line: str):
    fields = {}
    for token in line.split():
        name, separator, value = token.partition("=")
        if separator:
            fields[name] = value
    try:
        return {"event": int(fields["event"], 16), "correlation": int(fields["correlation"]),
                "sequence": int(fields["sequence"]), "arg0": int(fields["arg0"]), "arg1": int(fields["arg1"])}
    except (KeyError, ValueError):
        return None


def transaction_state_path(flight_text, transaction_uuid) -> dict:
    """The states ONE transaction passed through, in the ring's sequence order, from a read-flight answer.

    {read, transaction_uuid, states, transitions, refused_transitions, starts_at_created, terminal}. `states` is the
    first transition's state left, then every state entered; a refused transition is counted and never drawn as a state.
    A code this console does not name is printed as its number."""
    out = {"read": READ_FAILED, "transaction_uuid": None, "states": [], "transitions": 0, "refused_transitions": 0,
           "starts_at_created": None, "terminal": None}
    uuid = transaction_uuid.lower() if isinstance(transaction_uuid, str) else None
    if uuid is None or _UUID.fullmatch(uuid) is None:
        out["read"] = PATH_NO_TRANSACTION
        return out
    out["transaction_uuid"] = uuid
    parsed = cake_admin.parse_probe_output(flight_text if isinstance(flight_text, str) else "")
    if not parsed["ok"]:
        return out
    correlation = int.from_bytes(bytes.fromhex(uuid)[:8], "little")
    ours = [fields for fields in (_record_fields(line) for line in parsed["records"])
            if fields is not None and fields["correlation"] == correlation
            and fields["event"] in (EVT_TXN_TRANSITION, EVT_TXN_TRANSITION_REFUSED)]
    ours.sort(key=lambda fields: fields["sequence"])
    accepted = [fields for fields in ours if fields["event"] == EVT_TXN_TRANSITION]
    out["refused_transitions"] = len(ours) - len(accepted)
    if not accepted:
        out["read"] = PATH_NOT_IN_RING
        return out
    states = [_state_name(accepted[0]["arg0"])] + [_state_name(fields["arg1"]) for fields in accepted]
    out.update({"read": READ_OK, "states": states, "transitions": len(accepted),
                "starts_at_created": states[0] == TX_STATES[0],
                "terminal": states[-1] if states[-1] in TX_TERMINAL else None})
    return out


def flight_reader(probe_path: str, socket_path: str, *, timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S):
    """The read-flight text, through cake_admin's read-only allowlist, bound to one tool and one socket: the shape
    capability_check.CapabilityCheck takes as `read_flight`. An empty string is a failed read."""
    def read() -> str:
        answer = cake_admin.run_probe(probe_path, socket_path, "read-flight", timeout_s=timeout_s)
        return answer.get("text", "") if answer.get("ok") else ""
    return read


def read_slots_text(probe_path: str, socket_path: str, *, timeout_s: float = cake_admin.DEFAULT_TIMEOUT_S) -> str:
    """The `list-slots` text, for a per-slot reader to parse. A read, through the read-only client, not a mutation.

    An empty string is a failed read and is NOT an empty slot table: a caller that treated the two as the same would
    report a capability inert because a socket was down.
    """
    answer = cake_admin.run_probe(probe_path, socket_path, "list-slots", timeout_s=timeout_s)
    return answer.get("text", "") if answer.get("ok") else ""


# ---- reading the slot back, which is the ONLY thing that says an activation happened ----------------------------
#
# These two are READS and they are here rather than in cake_admin because they exist for one purpose: to confirm the
# mutation above. `status ok` does not say the update happened and neither does a completed transaction; the slot,
# read back, does. They mirror autonomy/motion/cake_capability_adapter.parse_slots and its availability rule, and are
# duplicated for the same reason parse_activation_answer is: `demo_runtime` is a packaged runtime that does not import
# `autonomy`. A test keeps the two readings equal.


def parse_slots(listing: str) -> dict:
    """The `slot ...` lines of an admin answer, by slot uuid.

    A line this parser cannot read whole is SKIPPED rather than half-read. A slot whose state was not understood must
    not be reported ACTIVE by default: reporting a capability available on a misparsed line is the failure this reader
    exists to avoid.
    """
    slots: dict = {}
    for raw in listing.splitlines():
        line = raw.strip()
        if not line.startswith("slot "):
            continue
        fields = {}
        for token in line[len("slot "):].split():
            name, separator, value = token.partition("=")
            if separator:
                fields[name] = value
        if not {"uuid", "name", "capsule", "generation", "state"} <= set(fields):
            continue
        try:
            generation = int(fields["generation"])
        except ValueError:
            continue
        slots[fields["uuid"]] = {"uuid": fields["uuid"], "name": fields["name"], "capsule": fields["capsule"],
                                 "generation": generation, "instance": fields.get("instance", ""),
                                 "state": fields["state"]}
    return slots


def capability_is_active(listing: str, *, slot_uuid: str, active_capsule: str) -> bool:
    """Whether ONE slot, named by its uuid, is ACTIVE on the capsule that carries the capability.

    THREE NEAR-MISSES ARE REFUSED BY CONSTRUCTION, each of which would report the capability available before anything
    was activated:
      - the HOLDER slot carries the same capsule by design, so the update engine has an admitted mapping to activate
        onto. Only this slot uuid is looked at and every other slot is ignored, whatever it carries;
      - the capability slot is reported ACTIVE while it still runs the INERT build, so a lifecycle word alone decides
        nothing: the capsule has to be the active one as well;
      - a failed read is an empty string, and an empty listing is answered False rather than treated as an empty slot
        table. The `status ok` line has to be there, because a tool that printed a refusal has not listed any slot.
    """
    if not isinstance(listing, str) or "status ok" not in listing:
        return False
    slot = parse_slots(listing).get(slot_uuid)
    return slot is not None and slot["state"] == ACTIVE and slot["capsule"] == active_capsule


# ---- what one read-back said, kept for the record ---------------------------------------------------------------
#
# capability_is_active answers the one question a mission may act on. slot_reading keeps what the SAME listing said
# around that answer, so the record can show which resident answered (its session), under which Plan and epoch, and
# what the slot carried (its capsule, generation and instance) before and after an activation. It decides nothing that
# capability_is_active does not already decide, and it calls capability_is_active on the same text so the two cannot
# disagree about one read.

_HEX_TOKEN = re.compile(r"[0-9a-f]{8,128}")
_WORD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DECIMAL = re.compile(r"[0-9]{1,20}")

#: How a read went. "ok" is a listing that said `status ok`; "failed" is a read that produced no such listing (the tool
#: could not run, the socket was down, the resident refused), which is NOT an empty slot table; "slot_absent" is a
#: listing that answered and did not name the slot.
READ_OK, READ_FAILED, READ_SLOT_ABSENT = "ok", "failed", "slot_absent"


def _decimal_or_none(value):
    """A decimal count as the resident printed it, as a STRING, or None. A string because an epoch, a generation, a
    transaction handle and an instance handle are u64 values and a browser cannot hold one as a number."""
    text = value if isinstance(value, str) else str(value) if isinstance(value, int) and not isinstance(value, bool) else ""
    return text if _DECIMAL.fullmatch(text) else None


def _token_or_none(value, shape):
    return value if isinstance(value, str) and shape.fullmatch(value) else None


def slot_reading(listing: str, *, slot_uuid: str, active_capsule: str) -> dict:
    """What ONE `list-slots` answer says about ONE slot and about the resident that answered it.

    THE SAME TEXT DECIDES AND IS RECORDED. `capability_active` is capability_is_active on this listing, so a record that
    carries this reading carries the read that decided, not a second read taken a moment later that may disagree.

    Every value is a fixed shape (hex digests and uuids, a word, a decimal count) or None. A value that does not have
    its shape is dropped rather than carried: this is shown to a console, and the resident's text is not a surface.
    """
    header = cake_admin.parse_probe_output(listing if isinstance(listing, str) else "")
    fields = header.get("fields", {})
    resident = {"session_uuid": _token_or_none(fields.get("session_uuid"), _HEX_TOKEN),
                "plan_digest": _token_or_none(fields.get("plan_digest"), _HEX_TOKEN),
                "epoch": _decimal_or_none(fields.get("epoch"))}
    reading = {"read": READ_FAILED, "slot_uuid": slot_uuid, "name": None, "state": None, "capsule": None,
               "generation": None, "instance": None, **resident, "capability_active": False}
    if not isinstance(listing, str) or "status ok" not in listing:
        return reading
    slot = parse_slots(listing).get(slot_uuid)
    if slot is None:
        reading["read"] = READ_SLOT_ABSENT
        return reading
    reading.update({
        "read": READ_OK,
        "name": _token_or_none(slot["name"], _WORD_TOKEN),
        "state": _token_or_none(slot["state"], _WORD_TOKEN),
        "capsule": _token_or_none(slot["capsule"].lower(), _HEX_TOKEN),
        "generation": _decimal_or_none(slot["generation"]),
        "instance": _decimal_or_none(slot.get("instance")),
        "capability_active": capability_is_active(listing, slot_uuid=slot_uuid, active_capsule=active_capsule),
    })
    return reading
