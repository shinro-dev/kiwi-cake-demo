#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""cake_capability_adapter: the real adapter behind capability_registry's seam.

`autonomy/motion/capability_registry.py` says in its own docstring that it "is the seam a later session
replaces with a real adapter (polling Cake's admin socket per mobile-approach-spec.md section 6: slot listing
content identity + lifecycle + health)". This is that adapter. It keeps the same three methods, so
`hybrid_pick_place.py` does not change.

WHAT DECIDES THAT A CAPABILITY IS AVAILABLE. Exactly one thing: the CAPABILITY SLOT, named by its uuid, is
reported ACTIVE on the capsule identity that carries the capability. Three near-misses are refused by
construction, and each has a test:

  - THE HOLDER SLOT HOLDS THE SAME CAPSULE. The mobile-approach deployment pre-admits the active build in a
    holder slot so that the engine has an admitted mapping to replace onto. A reader that asked "is the active
    capsule present anywhere?" would answer yes before anything was activated, which is the whole point of the
    wait. Availability is bound to ONE slot uuid and every other slot is ignored, whatever it carries.
  - TRAFFIC IS NOT LIFECYCLE. An active capability legitimately publishes nothing while it waits for an
    observation, so silence never means inactive; and a stale publisher could still be emitting after a slot
    went inert, so noise never means active. No command, count or rate is read here.
  - THE COMMAND PREFIX IS A LABEL. `command_id` carries the producing identity for correlation, and it is
    never the authority: a consumer decides whether to use output from the SLOT's reported state, then uses the
    prefix to check the output it accepted came from the generation it believed was running.

INACTIVE MEANS NO USABLE OUTPUT. `usable_output_allowed()` is the property the rest of the system reads, and it
is false whenever the slot is not ACTIVE on the capability capsule, whether or not anything is publishing.

This module reads. It performs no mutation; `ActivationOperation` below builds and interprets one, and the
caller sends it.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from capability_registry import ADMITTED, REJECTED, Candidate  # noqa: E402,F401

#: The lifecycle word the resident reports for a slot that is running its implementation.
ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class SlotState:
    """One slot as the resident's listing reports it. Every field is the resident's own."""

    uuid: str
    name: str
    capsule: str
    generation: int
    instance: str
    state: str


def parse_slots(listing: str) -> dict:
    """The `slot ...` lines of an admin answer, by slot uuid.

    A line this parser cannot read whole is SKIPPED rather than half-read: a slot whose state was not
    understood must not be reported ACTIVE by default, and reporting a capability available on a
    misparsed line is the failure this whole module exists to avoid.
    """
    slots = {}
    for raw in listing.splitlines():
        line = raw.strip()
        if not line.startswith("slot "):
            continue
        fields = {}
        for token in line[len("slot "):].split():
            name, sep, value = token.partition("=")
            if sep:
                fields[name] = value
        if not {"uuid", "name", "capsule", "generation", "state"} <= set(fields):
            continue
        try:
            generation = int(fields["generation"])
        except ValueError:
            continue
        slots[fields["uuid"]] = SlotState(
            uuid=fields["uuid"], name=fields["name"], capsule=fields["capsule"],
            generation=generation, instance=fields.get("instance", ""), state=fields["state"],
        )
    return slots


class CakeCapabilityAdapter:
    """Availability for ONE capability, bound to one slot uuid and one capsule identity.

    `probe` returns the text of an admin read (`list-slots`). It is injected so this is testable without a
    socket and so the caller owns how the resident is reached.
    """

    def __init__(self, *, capability: str, slot_uuid: str, active_capsule: str, probe,
                 log=lambda event, **fields: None):
        self.capability, self.slot_uuid = capability, slot_uuid
        self.active_capsule = active_capsule
        self._probe, self.log = probe, log
        self.last: SlotState | None = None
        self.last_error: str | None = None

    def observe(self) -> SlotState | None:
        """Read the listing once. A failed read clears nothing and is reported; it is not an absent slot."""
        try:
            listing = self._probe()
        except Exception as error:  # noqa: BLE001 -- the class only: a message can name a path
            self.last_error = type(error).__name__
            return self.last
        if not isinstance(listing, str) or "status ok" not in listing:
            self.last_error = "admin_read_not_ok"
            return self.last
        self.last_error = None
        self.last = parse_slots(listing).get(self.slot_uuid)
        return self.last

    def has_capability(self, name: str) -> bool:
        """True only for THIS capability, and only when its own slot is ACTIVE on the capability capsule."""
        if name != self.capability:
            return False
        slot = self.last
        return slot is not None and slot.state == ACTIVE and slot.capsule == self.active_capsule

    def usable_output_allowed(self) -> bool:
        """The property the consumer reads. Inactive means no usable output, whatever is on the wire."""
        return self.has_capability(self.capability)

    def running_identity(self) -> str | None:
        """The identity a consumer expects to see labelled on output it accepts, or None when none should be."""
        if not self.usable_output_allowed() or self.last is None:
            return None
        return f"{self.capability}.{self.last.capsule[:16]}.gen{self.last.generation}"

    def accepts_command_from(self, command_id: str) -> bool:
        """Whether an accepted command's correlation label matches the generation believed to be running.

        A LABEL CHECK, NOT AN AUTHORITY. `usable_output_allowed()` has already decided whether any output may
        be used; this only catches output produced by a generation that is no longer the one running, which is
        what a publisher that outlived its slot looks like.
        """
        identity = self.running_identity()
        return identity is not None and isinstance(command_id, str) and command_id.startswith(f"{identity}.")

    def wait_for_change(self, timeout=None) -> None:
        """The seam's third method. The real adapter polls rather than blocking, so a caller drives observe()
        on its own schedule; this exists so the interface matches and is deliberately not a sleep."""
        return None


# ---- the activation operation -------------------------------------------------------------------------------

#: What an activation answer means, kept apart from how the request itself went.
PENDING, ACCEPTED, REJECTED_BY_CAKE, REQUEST_FAILED = "PENDING", "ACCEPTED", "REJECTED_BY_CAKE", "REQUEST_FAILED"
#: The resident answered `status mutation_refused` with a refusal class: it judged the candidate and opened no
#: transaction (demo_runtime.cake_activation names the classes; a test keeps the two readings equal).
REFUSED_BEFORE_TRANSACTION = "REFUSED_BEFORE_TRANSACTION"


@dataclass(frozen=True)
class PreparedCandidate:
    """THE SUPPORTED ACTIVATION for the mobile-approach deployment: ApplyPlan with the prepared candidate Plan.

    MEASURED, NOT ASSUMED. `ReplaceSlot` naming the capability slot and the mobile capsule is answered
    `transaction_phase ABORTED`, on a freshly started resident with no prior transaction, and the slot is left
    exactly as it was. `ApplyPlan` carrying the swapped candidate Plan that `demo-plan build-mobile` prepares
    alongside the starting Plan completes, moves the capability slot from the inert capsule to the mobile one,
    advances its generation, and the capability slot's own supervisor log goes from EMPTY to carrying its
    child's output. The candidate swaps the capability and holder slots together, which is why one operation
    reaches it and a single-slot replacement does not.

    The candidate is PREPARED, not uploaded: its Plan and both capsules were admitted when the resident
    started, and `AdmitCapsule` is answered unavailable, so nothing here supplies new bytes to a running
    resident. The activation chooses between builds the resident already holds.

    The same operation-identity rule applies as for any mutation; see ActivationRequest.
    """

    operation_uuid: str
    candidate_plan: bytes
    operation: str = "apply-plan"

    def payload(self) -> bytes:
        return self.candidate_plan

    def is_retry_of(self, other: "PreparedCandidate") -> bool:
        return self.operation_uuid == other.operation_uuid and self.payload() == other.payload()


@dataclass(frozen=True)
class ActivationRequest:
    """A single-slot replacement: which slot, onto which capsule, under which operation identity.

    NOT the supported activation for the mobile-approach deployment; see PreparedCandidate. It is kept because
    the operation-identity rule below is the same for every mutation and is tested here.

    THE OPERATION UUID RULE, which the resident enforces and this encodes so a caller cannot get it wrong:
      - a NEW intended operation gets a FRESH uuid;
      - a RETRY of the same operation reuses the SAME uuid and MUST carry an identical payload;
      - the same uuid with a different payload is refused by the resident and is a caller defect;
      - after a reconnect the operation's STATE is retrieved, never resubmitted on a guess.
    """

    operation_uuid: str
    slot_uuid: str
    capsule: str

    def payload(self) -> bytes:
        """The 48 bytes ReplaceSlot carries: slot uuid then capsule digest."""
        return bytes.fromhex(self.slot_uuid) + bytes.fromhex(self.capsule)

    def is_retry_of(self, other: "ActivationRequest") -> bool:
        """A retry is the same identity AND the same bytes. Same identity with different bytes is not a retry."""
        return self.operation_uuid == other.operation_uuid and self.payload() == other.payload()


def parse_activation_answer(answer: str) -> dict:
    """What an activation answer says, with the request's own failure kept apart from the transaction's.

    `status ok` DOES NOT MEAN THE UPDATE HAPPENED. The dispatcher answers STATUS_OK for a transaction record
    without consulting its phase, so an ABORTED transaction arrives under `status ok`; this was observed
    live. `transaction_phase` is the only thing that decides the outcome, and a caller that read `status`
    would call a refusal a success. A request-level failure (a refused frame, a mismatched operation
    identity) is a different kind of event and is reported as REQUEST_FAILED, never as a rejection by Cake
    of the candidate. A `mutation_refused` answer WITH its refusal class is the one request-level status that is
    Cake judging the candidate: REFUSED_BEFORE_TRANSACTION.
    """
    fields = {}
    for raw in answer.splitlines():
        name, sep, value = raw.strip().partition(" ")
        if sep:
            fields[name] = value.strip()
    status = fields.get("status")
    phase = fields.get("transaction_phase")
    out = {"status": status, "transaction_phase": phase,
           "transaction_uuid": fields.get("transaction_uuid"), "reason_code": fields.get("refusal_code")}
    code = fields.get("refusal_code")
    if status == "mutation_refused" and phase is None and isinstance(code, str) and re.fullmatch(r"[0-9]{1,20}", code):
        # Cake judged the candidate before opening any transaction, and the class says why. That is not a request
        # that failed to land, and not a transaction that aborted.
        out["outcome"] = REFUSED_BEFORE_TRANSACTION
        out["detail"] = f"the resident refused the candidate before any transaction (refusal class {code})"
        return out
    if status != "ok":
        # The request itself did not land. This is not Cake judging the candidate.
        out["outcome"] = REQUEST_FAILED
        out["detail"] = status or "no_status"
        return out
    if phase is None:
        out["outcome"] = REQUEST_FAILED
        out["detail"] = "the answer carried no transaction phase"
        return out
    if phase == "COMPLETED":
        out["outcome"] = ACCEPTED
    elif phase == "ABORTED":
        out["outcome"] = REJECTED_BY_CAKE
        # The honest cause for this deployment: the candidate is not a capsule this resident admitted. It is
        # not an upload, not a signature failure and not a corrupted binary, and must never be labelled one.
        out["detail"] = "the candidate is not a capsule this resident admitted"
    else:
        out["outcome"] = PENDING
        out["detail"] = phase
    return out


def activation_confirmed(adapter: CakeCapabilityAdapter, answer: dict) -> bool:
    """An activation is confirmed by the OBSERVED SLOT, not by the answer.

    The answer says a transaction completed. Whether the capability is available is a property of the slot,
    read back, and that is what the mission acts on.
    """
    if answer.get("outcome") != ACCEPTED:
        return False
    adapter.observe()
    return adapter.usable_output_allowed()


# ---- binding a command to the process Cake launched -------------------------------------------------------------

#: The largest pid Linux can hand out (PID_MAX_LIMIT on a 64-bit kernel). A health arg1 above it is not a pid: the
#: rows of slots whose module is not a supervisor carry that module's own words there.
PID_MAX_LIMIT = 4_194_304


@dataclass(frozen=True)
class HealthRow:
    """One `health ...` row of a get-health answer. Every field is the resident's own; a number stays None when
    the row did not carry it as digits."""

    slot: str
    surface: str
    state_name: str
    arg0: int | None
    arg1: int | None
    sequence: int | None


def parse_health(answer: str) -> dict:
    """The `health ...` rows of a get-health answer, by slot uuid. A row without a slot is skipped."""
    rows = {}
    for raw in answer.splitlines():
        line = raw.strip()
        if not line.startswith("health "):
            continue
        fields = {}
        for token in line[len("health "):].split():
            name, sep, value = token.partition("=")
            if sep:
                fields[name] = value
        if not fields.get("slot"):
            continue

        def number(name):
            value = fields.get(name, "")
            return int(value) if value.isdigit() else None

        rows[fields["slot"]] = HealthRow(
            slot=fields["slot"], surface=fields.get("surface", ""), state_name=fields.get("state_name", ""),
            arg0=number("arg0"), arg1=number("arg1"), sequence=number("sequence"),
        )
    return rows


@dataclass(frozen=True)
class PidBinding:
    """Whether a command is bound to the process Cake launched for the capability slot, and why."""

    accepted: bool
    reason: str
    pid: int | None = None
    expected_prefix: str | None = None


def pid_prefix(capability: str, pid: int) -> str:
    """The command_id prefix a capability process started as `--identity <capability>.pid$$` puts on its output."""
    return f"{capability}.pid{pid}."


def bind_command_to_launched_process(command_id, *, capability: str, slot, active_capsule: str, health) -> PidBinding:
    """Accept a command only when it came from THE PROCESS CAKE LAUNCHED for the capability slot.

    ADDED BESIDE the generation-based `accepts_command_from`, which it does not change. That method matches a label
    of the form `<capability>.<capsule prefix>.gen<N>`; the deployed wrapper instead labels every command with the
    service's own pid, `<capability>.pid<P>.`, because the wrapper execs the service and so its `$$` is the child pid
    the supervisor slot reports. The binding holds only when ALL of these hold, and each refusal names its reason:

      - `slot` (the capability slot as list-slots reported it) is ACTIVE on `active_capsule`. The slot decides first,
        exactly as `usable_output_allowed` does: a pid never makes an inert slot usable;
      - `health` (parse_health of a get-health answer, or the one row for that slot) carries a `reported` row for
        that slot whose arg1 is a plausible pid, which is the process the supervisor launched;
      - `command_id` starts with `<capability>.pid<arg1>.`, the trailing dot included, so pid 12 never matches 123.

    A LABEL CHECK AGAINST CAKE'S OWN REPORT. It says the command carries the launched process's pid; it does not
    authenticate the sender, which any local process could imitate, and it is never a reason to actuate anything.
    """
    if slot is None:
        return PidBinding(False, "slot_absent")
    if slot.state != ACTIVE:
        return PidBinding(False, "slot_not_active")
    if slot.capsule != active_capsule:
        return PidBinding(False, "slot_not_on_capability_capsule")
    row = health.get(slot.uuid) if isinstance(health, dict) else health
    if row is None or getattr(row, "slot", None) != slot.uuid:
        return PidBinding(False, "health_row_absent")
    if row.surface != "reported":
        return PidBinding(False, "health_not_reported")
    if not row.arg1:
        return PidBinding(False, "health_pid_absent")
    if row.arg1 <= 1 or row.arg1 > PID_MAX_LIMIT:
        return PidBinding(False, "health_arg1_not_a_pid")
    expected = pid_prefix(capability, row.arg1)
    if not isinstance(command_id, str):
        return PidBinding(False, "command_id_not_text", row.arg1, expected)
    if not command_id.startswith(expected):
        return PidBinding(False, "command_not_from_launched_process", row.arg1, expected)
    return PidBinding(True, "bound_to_launched_process", row.arg1, expected)
