# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""demo_runtime.cli: kiwi-demo, the command line of the runtime daemon.

    python3 -m demo_runtime.cli [--socket PATH | --data-dir DIR] status
    python3 -m demo_runtime.cli ... run cycle [--plan-only]
    python3 -m demo_runtime.cli ... run observe [--holding]
    python3 -m demo_runtime.cli ... run return [--plan-only]
    python3 -m demo_runtime.cli ... run scan [--plan-only]
    python3 -m demo_runtime.cli ... stop
    python3 -m demo_runtime.cli ... result COMMAND_ID
    python3 -m demo_runtime.cli ... resolve COMMAND_ID FINDING [--note TEXT]

Everything goes through the SAME socket the console uses, so the console, a manual action and this command line share ONE
command authority: a cycle started here blocks Start in the browser, and the other way round. The command line starts no
controller by itself and holds no state.

`run` prints the command_id BEFORE it sends the command. When the answer is lost (the daemon died, the connection broke)
whether the command was accepted is not known: ask `result COMMAND_ID`. The command is never sent twice.

`status` also shows the loopback forwards the runtime owns (one `ssh -N -L` child per purpose, with the state and reason
of each) and the host command lease: whether this runtime HOLDS one, its generation and host session, how long it has
left, and the reason when it holds none. A lease the runtime does not hold is never shown as held, so a command refused
with lease_unavailable can be explained without opening anything. Beside them it shows `execution`: which of the two
backends commands take to the robot, and what actually owns the robot under it. On the baseline backend that is this
runtime's own single run slot and nothing on the robot, which is stated rather than left to be read out of a lease that
was never taken.

`resolve` does not clear anything by itself: the runtime re-checks the evidence (a live process with that identity, the
run's own terminal record, what the observation store gained after the command was accepted), classifies what it finds,
and records the finding as an ATTESTATION beside it. A finding that contradicts the evidence is refused, and evidence
that could not be read leaves every command blocked. `status` shows the same evidence and the question being asked.

Exit status: 0 done, 1 the runtime refused (or the command was rejected or is uncertain, or a resolve did not clear the
block), 2 the runtime cannot be reached or the answer was lost, 3 this client is read-only because the runtime speaks
another contract version.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .rpc import ReadOnlyClient, ResponseLost, RuntimeClient, RuntimeUnavailable

SOCKET_ENV_VAR = "KC_RUNTIME_SOCKET"
RUN_KINDS = {"cycle": ("start_cycle", "plan_only"), "observe": ("observe", "holding"), "return": ("return_arm", "plan_only"), "scan": ("scan_scene", "plan_only")}


def _socket_path(args) -> str:
    if args.socket:
        return os.path.expanduser(args.socket)
    if args.data_dir:
        return os.path.join(os.path.expanduser(args.data_dir), "runtime.sock")
    if os.environ.get(SOCKET_ENV_VAR):
        return os.path.expanduser(os.environ[SOCKET_ENV_VAR])
    raise SystemExit(f"kiwi-demo: name the runtime socket with --socket, --data-dir or ${SOCKET_ENV_VAR}")


def _show(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True))


def _status(client: RuntimeClient) -> int:
    snapshot, status = client.snapshot(), client.status()
    _show({"runtime_session": snapshot.get("runtime_session"), "data_mode": snapshot.get("data_mode"), "owner": snapshot.get("owner"), "active_run": snapshot.get("active_run"),
           "last_outcome": snapshot.get("last_outcome"), "evidence": snapshot.get("evidence"), "seq_cursor": snapshot.get("seq_cursor"), "retained_from_seq": snapshot.get("retained_from_seq"),
           "blocked": {item["id"]: item["reason_code"] for item in snapshot.get("capabilities", []) if item.get("state") != "enabled" and not item.get("planned")},
           "counters": status.get("counters"), "unresolved": status.get("unresolved"), "hardware_commands_enabled": status.get("hardware_commands_enabled"),
           "link": snapshot.get("link"), "sources": status.get("sources"), "unresolved_command": snapshot.get("unresolved_command"),
           "forwards": status.get("forwards"), "lease": status.get("lease"), "execution": status.get("execution")})
    return 0


def _run(client: RuntimeClient, args) -> int:
    command_type, flag = RUN_KINDS[args.kind]
    command = client.new_command(command_type, {flag: bool(args.plan_only if flag == "plan_only" else args.holding)})
    print(f"command_id {command['command_id']}", flush=True)  # BEFORE sending: it is the handle of a lost answer
    answer = client.submit(command)
    _show(answer)
    return 0 if answer.get("status") in ("accepted", "spawning", "running", "finished") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kiwi-demo", description="Talk to the demo runtime daemon through its socket.")
    parser.add_argument("--socket", default=None, help=f"path of the runtime socket (default: <--data-dir>/runtime.sock, else ${SOCKET_ENV_VAR})")
    parser.add_argument("--data-dir", default=None, help="the daemon's data directory")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status", help="owner, active run, last outcome, what is blocked and why, counters")
    run = actions.add_parser("run", help="submit one command")
    run.add_argument("kind", choices=sorted(RUN_KINDS))
    run.add_argument("--plan-only", action="store_true", help="cycle, return, scan: plan and report without sending a motion command")
    run.add_argument("--holding", action="store_true", help="observe: the cube is held")
    actions.add_parser("stop", help="stop the active run (SIGINT to the controller, never more)")
    result = actions.add_parser("result", help="what became of a command; the only way to resolve a lost answer")
    result.add_argument("command_id")
    resolve = actions.add_parser("resolve", help="declare what you found for an uncertain command; the runtime reconciles it against the evidence and may refuse")
    resolve.add_argument("command_id")
    resolve.add_argument("finding", help="no_controller_running | controller_finished | controller_stopped_by_operator")
    resolve.add_argument("--note", default=None, help="a short printable note kept with the attestation (200 characters at most)")
    args = parser.parse_args(argv)
    client = RuntimeClient(_socket_path(args), client_session=f"cli-{os.getpid()}")
    try:
        if args.action == "status":
            return _status(client)
        if args.action == "run":
            return _run(client, args)
        if args.action == "stop":
            answer = client.stop()
        elif args.action == "result":
            answer = client.result(args.command_id)
        else:
            answer = client.resolve(args.command_id, args.finding, args.note)
        _show(answer)
        # A resolve that was accepted but did NOT clear the block (the evidence could not be read) is not a success for
        # the operator: it ends non-zero, so a script cannot take it for an unblocked runtime.
        refused = answer.get("ok") is False or answer.get("status") in ("rejected", "uncertain") or (args.action == "resolve" and not answer.get("cleared"))
        return 1 if refused else 0
    except ResponseLost as lost:
        print(f"kiwi-demo: the answer was lost; whether the command was accepted is NOT known. Ask: result {lost.command_id}", file=sys.stderr)
        return 2
    except ReadOnlyClient:
        print("kiwi-demo: the runtime speaks another contract version; this client is read-only", file=sys.stderr)
        return 3
    except RuntimeUnavailable:
        print("kiwi-demo: the runtime socket cannot be reached", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
