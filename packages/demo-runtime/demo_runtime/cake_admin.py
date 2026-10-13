# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.cake_admin: a READ-ONLY client of a real Cake resident's admin socket.

It runs the shipped `admin-probe` as a child and parses what it prints. It never opens the admin socket itself and
never speaks the wire protocol: the protocol belongs to Cake, `admin-probe` is the supported client for it, and
reimplementing it here would be this repository guessing at somebody else's format.

FOUR OPERATIONS, ALL READ-ONLY: `get-status`, `read-flight`, `list-slots` and `get-health`. Each one only asks the
resident what it holds; none of them takes a payload and none of them can change a slot. Nothing here can install,
activate, swap or roll back anything. There is no code path in this module that submits a mutation, and contract v1
has no command that could reach one.

THE MUTATION LIVES SOMEWHERE ELSE, ON PURPOSE. An activation is an ApplyPlan carrying a prepared candidate, and it is
submitted by `demo_runtime.cake_activation`, a module that says in its own name that it mutates. Keeping the two apart
is what lets this one keep the paragraph above: a reader auditing whether the console can move a slot has one module to
read, not two.

WHAT A CONNECTION DOES AND DOES NOT MEAN. Reaching a resident says a resident is running and answering. It says
NOTHING about whether any module is active, and nothing at all about whether this robot's task is being carried by
Cake. Those are three separate facts and this module keeps them separate: `reachable`, `slots`, and a participation
flag that an admin socket cannot answer at all.

THE BINARY IS NAMED, NEVER SEARCHED FOR. A caller passes an absolute path to `admin-probe`. Nothing here looks on
PATH or in a working directory, so a test double in a repository cannot be picked up in place of the real tool by
accident; `tests/mock-bin` exists to be chosen deliberately, not found.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone

DEFAULT_TIMEOUT_S = 5.0
# The clock every time in this module is read from, by the name common.schema.json gives it. A wall stamp is for an
# operator to read; an AGE is measured on the monotonic one, because a wall clock can step and an age that went
# backwards would make a stale answer look fresh.
CLOCK_DOMAIN = "laptop_monotonic"
# The fields get-status prints that this console understands. A field outside this set is carried through as text
# rather than dropped, but only these are given a meaning anywhere.
STATUS_IDENTITY_FIELDS = ("build_identity", "session_uuid", "config_identity", "target_profile_digest", "plan_digest")
STATUS_COUNT_FIELDS = ("epoch", "slots", "bindings", "resources", "replay", "snapshot_present")
# The operations this module will run, and the whole of them. Every one is a question: it carries no payload, names no
# candidate and cannot move a slot. `list-slots` is what a per-slot reader needs to decide that a capability is
# available, and `get-health` answers the same identity fields as `get-status` from the resident's own health path.
# An operation outside this tuple is refused here rather than passed to the tool, so adding a mutation means editing
# this line and the module docstring above it, which is the point.
READ_OPERATIONS = ("get-status", "read-flight", "list-slots", "get-health")


def parse_probe_output(text: str) -> dict:
    """admin-probe's line format, as it prints it: `status ok` then `name value` per line.

    Numbers that are counts come back as ints; everything else stays the text the tool printed, because a digest that
    was reformatted is no longer the digest the tool reported. A `record ...` line of read-flight is kept whole in a
    list: its fields are Cake's, not this console's, and nothing here interprets them.
    """
    out: dict = {"ok": False, "fields": {}, "records": [], "lines": 0}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        out["lines"] += 1
        if line == "status ok":
            out["ok"] = True
            continue
        if line.startswith("record "):
            out["records"].append(line[len("record "):])
            continue
        name, _, value = line.partition(" ")
        if not name:
            continue
        value = value.strip()
        if name in STATUS_COUNT_FIELDS or name.startswith("flight_sequence"):
            try:
                out["fields"][name] = int(value)
                continue
            except ValueError:
                pass
        out["fields"][name] = value
    return out


def run_probe(probe_path: str, socket_path: str, operation: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """One read-only admin-probe request. Never raises: a failure is a dict with `ok` false and a reason code."""
    if operation not in READ_OPERATIONS:
        return {"ok": False, "reason_code": "operation_not_read_only", "fields": {}, "records": []}
    if not probe_path or not os.path.isabs(probe_path) or not os.access(probe_path, os.X_OK):
        return {"ok": False, "reason_code": "probe_binary_unavailable", "fields": {}, "records": []}
    if not socket_path or not os.path.exists(socket_path):
        return {"ok": False, "reason_code": "admin_socket_absent", "fields": {}, "records": []}
    try:
        finished = subprocess.run(
            [probe_path, "request", socket_path, operation],
            capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason_code": "admin_probe_timeout", "fields": {}, "records": []}
    except OSError:
        return {"ok": False, "reason_code": "admin_probe_unrunnable", "fields": {}, "records": []}
    parsed = parse_probe_output(finished.stdout)
    # The tool's own printed lines, kept for a reader that parses a shape this module does not model. `list-slots`
    # prints one `slot ...` line per slot and the per-slot reader in autonomy/motion parses those itself. It is
    # INTERNAL: no answer that leaves this runtime carries it, because a tool's text is not a surface.
    parsed["text"] = finished.stdout
    if not parsed["ok"]:
        # The tool's own refusal text can name a path, so it is not carried; the reason code is.
        parsed["reason_code"] = "admin_probe_refused" if finished.returncode != 0 else "admin_answer_not_ok"
    return parsed


def observed_now() -> dict:
    """WHEN a read was taken, stamped by the thing that took it.

    It is stamped HERE rather than where an answer is later assembled, because a cached answer is assembled long after
    it was read and a time taken at assembly would report the read-back time. That is exactly how a stale answer comes
    to look current, so the stamp travels with the observation from the moment it exists.
    """
    return {"observed_wall_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "observed_mono_ns": time.monotonic_ns(), "clock_domain": CLOCK_DOMAIN}


def read_inventory(probe_path: str, socket_path: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    """What this console shows about a real resident. Read-only, and honest about each of the three facts.

    `reachable` means the resident answered. `slots` is what IT reports it holds, and zero is a real answer, not a
    failure to read. `mission_participation` is false HERE because an admin socket cannot answer it: whether a module
    of this resident is carrying part of this robot's task is a fact about the mission, and the mission surface is
    what reports it. This stays false rather than becoming a guess assembled from a slot count.
    """
    observed = observed_now()
    status = run_probe(probe_path, socket_path, "get-status", timeout_s=timeout_s)
    flight = run_probe(probe_path, socket_path, "read-flight", timeout_s=timeout_s) if status.get("ok") else None
    return inventory_of(status, flight, observed)


def inventory_of(status: dict, flight, observed: dict) -> dict:
    """The inventory shape read_inventory returns, built from answers that were already read. demo_runtime.robot_link
    reads more than these two and builds the SAME shape here, so the cake surfaces cannot tell the two readers apart."""
    if not status.get("ok"):
        return {
            "reachable": False,
            "reason_code": status.get("reason_code", "admin_answer_not_ok"),
            "identity": {}, "counts": {}, "flight": None, "mission_participation": False, **observed,
        }
    fields = status["fields"]
    flight = flight if isinstance(flight, dict) else {}
    return {
        "reachable": True,
        "reason_code": None,
        "identity": {name: fields.get(name) for name in STATUS_IDENTITY_FIELDS if fields.get(name) is not None},
        "counts": {name: fields.get(name) for name in STATUS_COUNT_FIELDS if isinstance(fields.get(name), int)},
        "transaction_state": fields.get("transaction_state"),
        "flight": None if not flight.get("ok") else {
            "records": len(flight["records"]),
            "oldest": fields.get("flight_sequence_oldest"),
            "next": fields.get("flight_sequence_next"),
        },
        # NOT derived, and not derivable from an admin socket: a resident answering says nothing about this task.
        "mission_participation": False,
        **observed,
    }


def configured_paths(config) -> tuple:
    """(admin-probe path, admin socket path) from the runtime configuration, or (None, None).

    Both must be named explicitly. Nothing is searched for and nothing is defaulted: a Cake connection that appeared
    because a binary happened to be on PATH would be a connection nobody chose.
    """
    if config is None:
        return (None, None)
    cake = getattr(config, "cake", None)
    if cake is None:
        return (None, None)
    # A CakeConfig from the runtime configuration, or a plain mapping from a test. Both are read the same way.
    if isinstance(cake, dict):
        probe, socket_path = cake.get("admin_probe_path"), cake.get("admin_socket_path")
    else:
        probe, socket_path = getattr(cake, "admin_probe_path", None), getattr(cake, "admin_socket_path", None)
    if not isinstance(probe, str) or not isinstance(socket_path, str) or not probe or not socket_path:
        return (None, None)
    return (probe, socket_path)


def unavailable(reason_code: str = "not_configured") -> dict:
    return {"reachable": False, "reason_code": reason_code, "identity": {}, "counts": {}, "flight": None,
            "mission_participation": False, **observed_now()}



# ---- whether a Cake module took part in a mission, read from the MISSION's own record -------------------------------
#
# The three facts this module keeps apart stay apart: `reachable` and the slots are what a resident answers, and
# participation is what a mission RECORDED. An admin socket cannot say it, so read_inventory keeps its constant false;
# this answers it from a mission view (demo_runtime.mission's fold) and from nothing else. Two records say it:
#   - a target's capability evidence (the controller's capability block, carried in the target's `evidence`): a
#     generation and a pid the relay's first pulse read from the capability slot, and at least one base command the
#     motion gateway APPLIED through it. Bound to a generation and a pid, it names which build carried the base;
#   - an activation the runtime recorded OBSERVED: the capability slot was read back carrying the capability for this
#     mission. It says Cake changed what the robot can do for this mission, not that a command was applied.
# Anything else, a rejected, refused, failed or unobserved activation included, is not participation.

PARTICIPATION_APPLIED, PARTICIPATION_OBSERVED = "capability_applied", "activation_observed"


def _plain_count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def mission_participation(mission_view, *, module_name=None) -> dict:
    """{value, evidence, basis, module, generation, pid, applied, target_id, mission_id} for ONE mission view.

    `value` is True only on one of the two records above; `evidence` is a sentence naming what it rests on, or why it
    is false. `module_name` is the configured capability's name (cake.capability.name); without it the sentence says
    "the capability module"."""
    out = {"value": False, "evidence": "", "basis": None, "module": module_name if isinstance(module_name, str) else None,
           "generation": None, "pid": None, "applied": None, "target_id": None, "mission_id": None}
    if not isinstance(mission_view, dict):
        out["evidence"] = "no mission is on record, so no module of any resident is carrying part of a task"
        return out
    out["mission_id"] = mission_view.get("mission_id") if isinstance(mission_view.get("mission_id"), str) else None
    named = out["module"] or "the capability module"
    for target in mission_view.get("targets") or ():
        evidence = target.get("evidence") if isinstance(target, dict) else None
        capability = evidence.get("capability") if isinstance(evidence, dict) else None
        if not isinstance(capability, dict):
            continue
        generation, pid, applied = (_plain_count(capability.get("generation")), _plain_count(capability.get("pid")),
                                    _plain_count(capability.get("applied")))
        if generation is None or pid is None or not applied:
            continue
        accepted = _plain_count(capability.get("accepted"))
        out.update({"value": True, "basis": PARTICIPATION_APPLIED, "generation": generation, "pid": pid, "applied": applied,
                    "target_id": target.get("target_id") if isinstance(target.get("target_id"), str) else None})
        out["evidence"] = (f"{named} (generation {generation}, pid {pid}) carried the base for target "
                           f"{out['target_id'] or 'unnamed'}: {applied} base commands relayed through it were applied by the "
                           f"motion gateway" + (f" of {accepted} it accepted" if accepted is not None else ""))
        return out
    observed = [activation for activation in mission_view.get("activations") or ()
                if isinstance(activation, dict) and activation.get("activation_state") == "OBSERVED"]
    if observed:
        key = observed[-1].get("candidate_key")
        out.update({"value": True, "basis": PARTICIPATION_OBSERVED})
        out["evidence"] = (f"a Cake activation for this mission ({'candidate ' + key if isinstance(key, str) else 'a prepared candidate'}) "
                           f"was read back in the capability slot carrying {named}")
        return out
    out["evidence"] = ("this mission's record shows no base command applied through a Cake module and no activation "
                       "observed in the capability slot")
    return out
