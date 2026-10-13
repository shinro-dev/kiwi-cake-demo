#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# ONE command that brings up the operator console on this laptop: the runtime daemon, then the HTTP surface, then the
# URL to open. Starting either of them NEVER contacts the robot, acquires a motor, enables torque or moves anything;
# the daemon only spawns a controller when a command arrives, and even then its preflight and ownership decide.
#
#   bin/laptop/start-console.sh synthetic        a scripted run, no robot and no configuration needed
#   bin/laptop/start-console.sh live             the real controller, refused unless a runtime configuration is given
#   bin/laptop/start-console.sh live --enable-hardware-commands    live, and commands that start the controller allowed
#   bin/laptop/start-console.sh replay           recorded runs only; no command path is constructed at all
#   bin/laptop/start-console.sh stop             stop what this script started
#
# Environment:
#   KC_RUNTIME_CONFIG   path of the PRIVATE runtime configuration (required for live and replay; never committed)
#   KC_CONSOLE_DATA     private data directory for runs, bundles and journals (default: under the user runtime dir)
#   KC_CONSOLE_PORT     loopback port for the console (default 8770)
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
MODE="${1:-synthetic}"; shift || true
PORT="${KC_CONSOLE_PORT:-8770}"
DATA="${KC_CONSOLE_DATA:-${XDG_RUNTIME_DIR:-/tmp}/kiwi-console}"
RUN_DIR="$DATA/.run"
PYTHONPATH="$ROOT/packages/demo-runtime:$ROOT/packages/demo-contracts/python:$ROOT/apps/console-api"
export PYTHONPATH

stop_one() {
  local name="$1"
  local pidfile="$RUN_DIR/$name.pid"
  [ -f "$pidfile" ] || return 0
  local pid; pid=$(cat "$pidfile")
  if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; fi
  rm -f "$pidfile"
  echo "stopped $name"
}

if [ "$MODE" = "stop" ]; then
  stop_one console-api
  stop_one runtime
  # The command is printed in full because an operator pastes it. "kiwi-demo" stood here and is not a command: it is
  # the name argparse prints in its own usage line, and there is no executable, no entry point and no script by that
  # name anywhere. Pasted at a shell it says "command not found", at the one moment somebody needs to reach a
  # controller that is still running.
  echo "the console is stopped; any controller it started keeps running."
  echo "  reach it with:  PYTHONPATH=$ROOT/packages/demo-runtime:$ROOT/packages/demo-contracts/python \\"
  echo "                  python3 -m demo_runtime.cli --data-dir $DATA status"
  echo "  and stop that controller with the same command, ending in: stop"
  exit 0
fi

case "$MODE" in
  synthetic|live|replay) ;;
  *) echo "usage: $0 [synthetic|live|replay|stop] [--enable-hardware-commands]" >&2; exit 2 ;;
esac

if [ "$MODE" != "synthetic" ] && [ -z "${KC_RUNTIME_CONFIG:-}" ]; then
  echo "REFUSED: $MODE mode needs KC_RUNTIME_CONFIG, the path of the private runtime configuration." >&2
  echo "         packages/demo-runtime/config.example.json shows the shape; keep the real one outside this checkout." >&2
  exit 3
fi

mkdir -p "$RUN_DIR"; chmod 700 "$DATA" "$RUN_DIR" 2>/dev/null || true

if [ -f "$RUN_DIR/runtime.pid" ] && kill -0 "$(cat "$RUN_DIR/runtime.pid")" 2>/dev/null; then
  echo "REFUSED: a runtime this script started is already running (pid $(cat "$RUN_DIR/runtime.pid"))." >&2
  echo "         Stop it with '$0 stop' first. A second runtime would be refused by the daemon's own lock anyway." >&2
  exit 3
fi

# The built console is optional: without it the API still serves, and the page is simply not there to open.
WEB_BUILD="$ROOT/apps/console-web/build"
if [ ! -f "$WEB_BUILD/index.html" ]; then
  echo "note: no built console at apps/console-web/build; serving the API alone."
  echo "      build it with: pnpm install && pnpm -F @kiwi/console-web build   (Node 22; see .nvmrc)"
  WEB_ARGS=()
else
  WEB_ARGS=(--web-build "$WEB_BUILD")
fi

DAEMON_ARGS=(--data-dir "$DATA")
[ -n "${KC_RUNTIME_CONFIG:-}" ] && DAEMON_ARGS+=(--config "$KC_RUNTIME_CONFIG")
case "$MODE" in
  synthetic) DAEMON_ARGS+=(--synthetic) ;;
  replay)    DAEMON_ARGS+=(--replay-only) ;;
esac
for arg in "$@"; do
  case "$arg" in
    --enable-hardware-commands)
      if [ "$MODE" != "live" ]; then echo "REFUSED: --enable-hardware-commands only means anything in live mode." >&2; exit 2; fi
      DAEMON_ARGS+=("$arg") ;;
    *) echo "REFUSED: unknown argument $arg" >&2; exit 2 ;;
  esac
done

nohup python3 -m demo_runtime.daemon "${DAEMON_ARGS[@]}" >"$RUN_DIR/runtime.log" 2>&1 &
echo $! > "$RUN_DIR/runtime.pid"

for _ in $(seq 1 50); do
  [ -S "$DATA/runtime.sock" ] && break
  sleep 0.2
done
if [ ! -S "$DATA/runtime.sock" ]; then
  echo "REFUSED: the runtime did not open its socket. Its log:" >&2
  tail -n 20 "$RUN_DIR/runtime.log" >&2 || true
  stop_one runtime
  exit 4
fi
head -n 1 "$RUN_DIR/runtime.log" || true

nohup python3 -m console_api --runtime-socket "$DATA/runtime.sock" --port "$PORT" "${WEB_ARGS[@]}" \
  >"$RUN_DIR/console-api.log" 2>&1 &
echo $! > "$RUN_DIR/console-api.pid"

for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:$PORT/api/hello" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
if ! curl -fsS "http://127.0.0.1:$PORT/api/hello" >/dev/null 2>&1; then
  echo "REFUSED: the console API did not answer on port $PORT. Its log:" >&2
  tail -n 20 "$RUN_DIR/console-api.log" >&2 || true
  stop_one console-api; stop_one runtime
  exit 4
fi
head -n 1 "$RUN_DIR/console-api.log" || true

echo
echo "  OPEN THIS:  http://127.0.0.1:$PORT/?source=live"
echo
echo "  The bare address without the query serves a scripted demonstration instead, on purpose: a page opened by"
echo "  accident must never start talking to something that can move a robot."
echo "  Mode: $MODE. Stop both with: $0 stop"
