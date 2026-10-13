#!/usr/bin/env python3
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""A stand-in for admin-probe that answers like the ROBOT's resident, in the real line shapes. It is not Cake.

    fake_robot_probe.py request SOCKET_PATH OPERATION

It first CONNECTS to SOCKET_PATH, exactly as the real client does, so a stale socket file that nothing listens on fails
here the way it fails there. The answers come from the directory <SOCKET_PATH>.state/:

    status   the get-status field lines (build_identity, session_uuid, ... flight_sequence_next)
    slots    `slot capsule=.. config=.. generation=.. instance=.. name=.. state=.. uuid=..` lines
    health   `health arg0=.. arg1=.. code=.. flags=.. sequence=.. slot=.. state=.. state_name=.. surface=reported` lines
    flight   `record ...` lines
    health_unavailable   present: get-health answers `status unavailable`, as the resident does when it cannot read one

Every request appends one admin-domain record (event 0x0b000001) with this process's pid as its subject, which is what
the real resident writes for every admin connection: the poll traffic a reader must never show as module activity.
It reads four operations and refuses every other one, including every mutation. An apply-plan that reaches it is
first recorded in <SOCKET_PATH>.state/apply_plan_attempts, so a test can prove that none did.

AN APPLY SCRIPT, OPT IN. Only when <SOCKET_PATH>.state/apply_script.json exists is an apply-plan ANSWERED instead of
refused, and then only as that file says, keyed by the sha256 of the payload the tool was handed (or "*" for any other):

    {"<sha256 hex>": {"phase": "COMPLETED" | "ABORTED" | "RUNNING", "sleep_s": 0.0, "transaction_handle": 17,
                      "epoch": 4, "files": {"slots": "...", "status": "...", "health": "..."}}}

`files` replaces those state files BEFORE the answer is printed, which is what a COMPLETED transaction that moved the
slot (or a resident that restarted under a new session) looks like to the next read. An entry that is missing, and a
payload the script does not name with no "*", is answered `status refused`. Without the file this tool behaves exactly
as it always has. It is still not Cake: it judges nothing and simulates only what a test writes down.
"""
import hashlib
import json
import os
import socket
import sys
import time

READ_OPERATIONS = ("get-status", "list-slots", "get-health", "read-flight")


def _read(state: str, name: str) -> str:
    try:
        with open(os.path.join(state, name), encoding="ascii") as handle:
            text = handle.read()
    except OSError:
        return ""
    return text if text.endswith("\n") or not text else text + "\n"


def main(argv) -> int:
    if len(argv) < 4 or argv[1] != "request":
        sys.stderr.write("usage: admin-probe request SOCKET_PATH OPERATION\n")
        return 1
    socket_path, operation = argv[2], argv[3]
    state = socket_path + ".state"
    if operation == "apply-plan" and os.path.isdir(state):
        # A MUTATION was attempted against this resident: recorded BEFORE anything else, so a test can prove none was,
        # and then refused like every other operation outside the four reads.
        with open(os.path.join(state, "apply_plan_attempts"), "a", encoding="ascii") as handle:
            handle.write(" ".join(argv[4:]) + "\n")
    if len(argv) != 4 and operation != "apply-plan":
        sys.stderr.write("usage: admin-probe request SOCKET_PATH OPERATION\n")
        return 1
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(1.0)
            peer.connect(socket_path)
    except OSError:
        sys.stderr.write("admin-probe: connect failed\n")
        return 1
    script = os.path.join(state, "apply_script.json")
    if operation == "apply-plan" and os.path.isfile(script):
        return _scripted_apply(state, script, argv[4:])
    if not os.path.isdir(state) or operation not in READ_OPERATIONS:
        sys.stderr.write("admin-probe: refused\n")
        return 1
    header = "status ok\nreplay 0\nsnapshot_present 1\n" + _read(state, "status")
    flight = _read(state, "flight")
    if operation == "get-status":
        out = header
    elif operation == "list-slots":
        out = header + _read(state, "slots")
    elif operation == "get-health":
        out = "status unavailable\n" if os.path.exists(os.path.join(state, "health_unavailable")) else header + _read(state, "health")
    else:
        out = "status ok\nreplay 0\nsnapshot_present 0\nflight_records " + str(flight.count("record ")) + "\n" + flight
    sys.stdout.write(out)
    with open(os.path.join(state, "flight"), "a", encoding="ascii") as handle:
        handle.write("record arg0=1000 arg1=1000 correlation=0 epoch=0 event=0x0b000001 flags=0 sequence=%d severity=0 subject=%d\n"
                     % (flight.count("record "), os.getpid()))
    return 0


def _option(arguments, name):
    return arguments[arguments.index(name) + 1] if name in arguments and arguments.index(name) + 1 < len(arguments) else None


def _scripted_apply(state: str, script_path: str, arguments) -> int:
    """An apply-plan answered from the opt-in script, in the answer lines the real tool prints."""
    payload_path, operation_uuid = _option(arguments, "--payload"), _option(arguments, "--uuid")
    try:
        with open(payload_path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        with open(script_path, encoding="ascii") as handle:
            script = json.load(handle)
    except (OSError, TypeError, ValueError):
        sys.stdout.write("status refused\nrefusal_code payload_unreadable\n")
        return 1
    entry = script.get(digest, script.get("*"))
    if not isinstance(entry, dict) or not operation_uuid:
        sys.stdout.write("status refused\nrefusal_code operation_not_scripted\n")
        return 1
    time.sleep(float(entry.get("sleep_s", 0.0)))
    for name, text in (entry.get("files") or {}).items():
        if name in ("slots", "status", "health"):
            with open(os.path.join(state, name), "w", encoding="ascii") as handle:
                handle.write(text)
    lines = ["status ok", "transaction_uuid " + operation_uuid[:8] + "-txn",
             "transaction_handle " + str(int(entry.get("transaction_handle", 17))),
             "transaction_phase " + str(entry.get("phase", "ABORTED"))]
    if entry.get("epoch") is not None:
        lines.append("epoch " + str(int(entry["epoch"])))
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
