# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.__main__: start the operator console's HTTP surface.

    python3 -m console_api --runtime-socket PATH [--port 8770] [--web-build DIR] [--credential-file PATH]

It binds 127.0.0.1 and nothing else. There is no LAN mode in v1: to reach the console from another machine, forward the
port over ssh and open the forwarded loopback address in the browser.

Starting this process acquires no motor, starts no controller, opens no camera and contacts no robot. It connects to the
runtime's socket only when a request arrives, so it starts (and says so) even when the runtime is not running yet, and it
can be stopped and started again in the middle of a run without touching the run.

The access log is off. A request line would put query strings into a log file, and nothing about a console request is
worth the risk of writing one down.

THE CREDENTIAL IS THE RUNTIME SESSION'S, not this launch's. Before it binds the port this process asks the runtime who
it is, once, with a short timeout, and takes that session's credential out of the runtime's own private directory,
creating it atomically if it is the first to ask. Restart this process against the same runtime and the same value comes
back, so a tab that was left open goes on commanding instead of being answered 403 until somebody reloads it. A runtime
that is not up yet cannot name a session, so this launch mints its own and says so on the ready line; it binds to the
first session it hears about afterwards, and an open tab is reloaded once at that point.
"""
from __future__ import annotations

import argparse
import os
import sys

from aiohttp import web

from . import API_VERSION, DEFAULT_PORT, LOOPBACK, ensure_runtime_importable
from .app import build_app
from .security import CredentialError, CredentialStore, default_credential_path, mint_credential, write_credential

ensure_runtime_importable()

RUNTIME_TIMEOUT_S = 10.0  # a unary call that does not answer in this time fails the request, never the loop
# One handshake before the port is bound. It is short on purpose: a runtime that is not there must not stop the API from
# starting, which is the property that lets the operator start the two in either order.
SESSION_PROBE_TIMEOUT_S = 3.0


def session_credential(store: CredentialStore, socket_path: str):
    """(credential, runtime_session). The session's own credential, or a fresh one when no runtime named a session."""
    from demo_runtime.rpc import RuntimeClient, RuntimeUnavailable

    prober = RuntimeClient(socket_path, client_session=f"console-api-probe-{os.getpid()}", timeout_s=SESSION_PROBE_TIMEOUT_S)
    try:
        answer = prober.hello()
    except (RuntimeUnavailable, OSError, ValueError):
        return mint_credential(), None
    session = answer.get("runtime_session") if isinstance(answer, dict) and answer.get("ok") is True else None
    if not isinstance(session, str) or session == "":
        return mint_credential(), None
    try:
        return store.credential_for(session), session
    except (CredentialError, OSError):
        return mint_credential(), None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m console_api", description="The operator console's HTTP surface: a forward to the runtime and the read models.")
    parser.add_argument("--runtime-socket", required=True, help="path of the runtime daemon's socket (<its data directory>/runtime.sock)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"loopback port to bind (default {DEFAULT_PORT})")
    parser.add_argument("--web-build", default=None, help="directory of the built console to serve at / (without it, only the API is served)")
    parser.add_argument("--credential-file", default=None, help="where to write this launch's credential (default: a 0600 file in the private per-user runtime directory)")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be a TCP port number")

    socket_path = os.path.abspath(os.path.expanduser(args.runtime_socket))
    web_build = os.path.abspath(os.path.expanduser(args.web_build)) if args.web_build else None
    # The runtime's data directory is the directory its socket lives in, and that is where a credential that belongs to
    # the runtime session belongs: private already, outside the repository already, and gone when that runtime's data
    # directory is.
    store = CredentialStore(os.path.dirname(socket_path))
    credential, runtime_session = session_credential(store, socket_path)
    try:
        # A COPY for a command line client, at the path this launch was told to use. It is this process's file and it
        # is removed when this process exits; the session's own credential file is the runtime's and stays.
        credential_path = write_credential(args.credential_file or default_credential_path(args.port), credential)
    except (CredentialError, OSError) as error:
        print(f"console-api: the credential file could not be written: {type(error).__name__}", file=sys.stderr)
        return 2

    from demo_runtime.rpc import RuntimeClient

    client = RuntimeClient(socket_path, client_session=f"console-api-{os.getpid()}", timeout_s=RUNTIME_TIMEOUT_S)
    app = build_app(client=client, credential=credential, web_build=web_build, host=LOOPBACK, port=args.port,
                    private_tokens=[socket_path, os.path.dirname(socket_path), str(credential_path), web_build or ""],
                    credential_store=store, runtime_session=runtime_session)

    async def forget_credential(_app):
        try:
            os.unlink(credential_path)
        except OSError:
            pass

    app.on_cleanup.append(forget_credential)
    owner = "owned by the runtime session, so a restart of this process does not log a tab out" if runtime_session else (
        "this launch's own: no runtime named a session yet, so a tab must be reloaded after a restart")
    print(f"console-api: ready (version {API_VERSION}; http://{LOOPBACK}:{args.port}; runtime socket "
          f"{'present' if os.path.exists(socket_path) else 'not there yet'}; web build {'attached' if web_build else 'not attached'}; "
          f"credential in a private 0600 file, {owner})", flush=True)
    try:
        web.run_app(app, host=LOOPBACK, port=args.port, access_log=None, print=None, shutdown_timeout=1.0)
    except OSError as error:
        print(f"console-api: the port could not be bound: {type(error).__name__}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
