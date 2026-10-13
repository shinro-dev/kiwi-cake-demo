#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/pi/start-camera-service.sh [start|status|stop|lock-status] [options]: deploy and run the single camera capture
# owner (bin/pi/camera_service.py) on the robot computer. It lives under bin/pi because what it starts runs there, but it
# is RUN ON THE LAPTOP over ssh. Unlike bin/pi/start-host.sh the service does NOT stay in this terminal: it is started
# detached on the robot computer and keeps running until 'stop'.
#
#   bin/pi/start-camera-service.sh               start the service: wrist camera only, preview ON
#   bin/pi/start-camera-service.sh status        ask the running service for its status (one JSON object)
#   bin/pi/start-camera-service.sh stop          SIGTERM, wait for it to close every device and release every lock
#   bin/pi/start-camera-service.sh lock-status   who holds each camera lock (opens no camera, needs no service)
#
# Options for 'start': --source-fps N (REQUEST this source rate from the wrist camera), --front-source-fps N (the same
# for the front camera: the source rate is what the shared USB hub carries), --preview-fps N, --no-preview, --enable-front.
#
# --enable-front IS NOT QUALIFIED. The front camera shares a USB hub with the servo adapter, two concurrent streams were
# reported unstable on this rig, and step 4 of docs/autonomous-demo/camera-service.md (the step that decides whether the
# front camera may stream at all) has not been run. Do not pass it in a session that also moves the robot.
#
# This script opens no camera itself and commands no motor. The service it starts opens the wrist camera and keeps it
# open; that is the one physical effect. Qualification of the service is docs/autonomous-demo/hardware-qualification.md
# (bin/laptop/qualify-robot.sh), and until it passes the legacy per-capture path stays the capture provider of record.
#
# What is copied, and checksummed, into a FIXED directory on the robot computer (kc-camera-service/deploy, under the
# remote account's home): bin/pi/camera_service.py, autonomy/perception/camera_lock.py and
# autonomy/perception/camera_lifecycle.py, each at the path the service imports it from. A deployed copy that differs
# from this checkout while a service is running is refused, never overwritten under the running process.
#
# Every identity comes from the environment, never from this file and never from a default:
#   KC_PI_SSH            the ssh target of the robot computer (key-based login; BatchMode never asks for a password)
#   KC_WRIST_CAMERA_ID   the stable identity of the wrist camera on the robot computer
#   KC_FRONT_CAMERA_ID   the same for the front camera, required ONLY with --enable-front
# Optional: KC_PI_PYTHON, an absolute path to the python of the lerobot install on the robot computer (default: the
# miniconda python in the remote account's home directory). The identity is passed in the remote process environment and
# is never printed, never written to a log line and never put in the service's argument vector.
# Exit status: 0 the action did what it says, 3 a preflight or a readiness check refused, 4 a deployed file does not
# match this checkout, 5 usage or a missing/unsafe identity, 6 the service did not answer, 255 ssh could not reach it.
set -euo pipefail

ACTION="start"
case "${1:-}" in
  start | status | stop | lock-status) ACTION="$1"; shift ;;
  -*) ;;
  '') ;;
  *) echo "usage: $0 [start|status|stop|lock-status] [--source-fps N] [--front-source-fps N] [--preview-fps N] [--no-preview] [--enable-front]" >&2; exit 5 ;;
esac

FRONT=0
SERVICE_OPTS=""
number() { case "$1" in '' | *[!0-9.]* | *.*.*) return 1 ;; *) return 0 ;; esac; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --enable-front) FRONT=1; shift ;;
    --no-preview) SERVICE_OPTS="$SERVICE_OPTS --no-preview"; shift ;;
    --source-fps | --preview-fps | --front-source-fps)
      number "${2:-}" || { echo "REFUSE: $1 takes a number" >&2; exit 5; }
      SERVICE_OPTS="$SERVICE_OPTS $1 $2"; shift 2 ;;
    *) echo "usage: $0 [start|status|stop|lock-status] [--source-fps N] [--front-source-fps N] [--preview-fps N] [--no-preview] [--enable-front]" >&2; exit 5 ;;
  esac
done
[ "$ACTION" = "start" ] || [ -z "$SERVICE_OPTS" ] || { echo "REFUSE: the service options apply to 'start' only" >&2; exit 5; }
[ "$ACTION" = "start" ] || [ "$FRONT" -eq 0 ] || { echo "REFUSE: --enable-front applies to 'start' only" >&2; exit 5; }

# Fail closed on identities. Every value below is placed inside single quotes in a command line that the REMOTE shell
# parses and nothing here escapes them, so a value carrying a quote, a space or a shell metacharacter is refused rather
# than sent. No value is ever printed.
MISSING=0
need() {
  local name="$1" what="$2"
  if [ -z "${!name:-}" ]; then echo "REFUSE: $name is not set ($what). Set it in your shell from your private, uncommitted runtime configuration." >&2; MISSING=1; return 0; fi
  case "${!name}" in
    -* | *[!A-Za-z0-9_./:@+=-]*) echo "REFUSE: $name starts with '-' or holds a character outside A-Za-z0-9_./:@+=- ; it is used inside a remote command line and is not escaped." >&2; MISSING=1 ;;
  esac
}
need KC_PI_SSH "the ssh target of the robot computer"
if [ "$ACTION" = "start" ]; then
  need KC_WRIST_CAMERA_ID "the stable identity of the wrist camera on the robot computer"
  [ "$FRONT" -eq 0 ] || need KC_FRONT_CAMERA_ID "the stable identity of the front camera on the robot computer"
fi
[ -z "${KC_PI_PYTHON:-}" ] || need KC_PI_PYTHON "the python of the lerobot install on the robot computer"
[ "$MISSING" -eq 0 ] || exit 5
case "${KC_PI_PYTHON:-/}" in /*) ;; *) echo "REFUSE: KC_PI_PYTHON must be an absolute path on the robot computer." >&2; exit 5 ;; esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 "$KC_PI_SSH")
if [ -n "${KC_PI_PYTHON:-}" ]; then REMOTE_PYTHON="'$KC_PI_PYTHON'"; else REMOTE_PYTHON="\"\$HOME/miniconda3/bin/python3\""; fi
REMOTE_DIR="kc-camera-service/deploy"  # FIXED, not per start: 'status', 'stop' and 'lock-status' must find the same copy
MISSION_PORT=5570
FILES=("bin/pi/camera_service.py" "autonomy/perception/camera_lock.py" "autonomy/perception/camera_lifecycle.py")

# ---------------------------------------------------------------------------------------------------------------------
# Deploy: copy nothing when the robot computer already has these exact bytes, and never overwrite under a running service
# ---------------------------------------------------------------------------------------------------------------------
remote_state() {  # prints 'running <0|1>' and one 'sum <path> <sha256|->' line per file
  # shellcheck disable=SC2029  # REMOTE_DIR is a constant of this script
  "${SSH[@]}" "DIR='$REMOTE_DIR' bash -s" <<'STATE'
set -uo pipefail
if pgrep -f 'camera_service[.]py' >/dev/null 2>&1; then echo "running 1"; else echo "running 0"; fi
for f in bin/pi/camera_service.py autonomy/perception/camera_lock.py autonomy/perception/camera_lifecycle.py; do
  if [ -f "$DIR/$f" ]; then echo "sum $f $(sha256sum "$DIR/$f" | cut -d' ' -f1)"; else echo "sum $f -"; fi
done
STATE
}

deploy() {
  local state running=0 stale=0 f local_sum remote_sum
  state="$(remote_state)"
  case "$state" in *"running 1"*) running=1 ;; esac
  for f in "${FILES[@]}"; do
    local_sum="$(sha256sum "$ROOT/$f" | cut -d' ' -f1)"
    remote_sum="$(printf '%s\n' "$state" | awk -v n="$f" '$1 == "sum" && $2 == n { print $3 }')"
    [ "$local_sum" = "$remote_sum" ] || stale=1
  done
  # Everything this function says goes to stderr: the stdout of 'lock-status' and 'status' is one JSON object and
  # nothing else, because bin/laptop/qualify-robot.sh parses it.
  if [ "$stale" -eq 0 ]; then echo "deploy: the robot computer already holds these exact bytes" >&2; return 0; fi
  [ "$running" -eq 0 ] || { echo "REFUSE: the deployed copy differs from this checkout and a camera service is RUNNING; stop it first ($0 stop)" >&2; exit 4; }
  echo "deploy: copying ${#FILES[@]} files and verifying each checksum" >&2
  # shellcheck disable=SC2029  # REMOTE_DIR is a constant of this script
  "${SSH[@]}" "mkdir -p '$REMOTE_DIR/bin/pi' '$REMOTE_DIR/autonomy/perception' kc-camera-service/logs"
  for f in "${FILES[@]}"; do
    [ -f "$ROOT/$f" ] || { echo "REFUSE: $f is missing from this checkout" >&2; exit 5; }
    scp -q -o BatchMode=yes -o ConnectTimeout=6 "$ROOT/$f" "$KC_PI_SSH:$REMOTE_DIR/$f"
  done
  state="$(remote_state)"
  for f in "${FILES[@]}"; do
    local_sum="$(sha256sum "$ROOT/$f" | cut -d' ' -f1)"
    remote_sum="$(printf '%s\n' "$state" | awk -v n="$f" '$1 == "sum" && $2 == n { print $3 }')"
    if [ -z "$local_sum" ] || [ "$local_sum" != "$remote_sum" ]; then echo "REFUSE: checksum mismatch between the local $f and the copy on the robot computer" >&2; exit 4; fi
  done
}

# One JSON request on the mission socket, from the robot computer's own loopback (the socket is bound there only).
ask() {
  local op="$1" timeout="$2" rc=0
  # shellcheck disable=SC2029  # every interpolated value is a constant or a number built here
  "${SSH[@]}" "PYBIN=$REMOTE_PYTHON PORT='$MISSION_PORT' OP='$op' WAIT='$timeout' bash -s" <<'ASK' || rc=$?
set -uo pipefail
T="$(mktemp)"; trap 'rm -f "$T"' EXIT
cat >"$T" <<'PYSRC'
import json, os, sys
import zmq
context = zmq.Context.instance()
socket = context.socket(zmq.REQ)
socket.setsockopt(zmq.LINGER, 0)
socket.setsockopt(zmq.SNDTIMEO, 3000)
socket.setsockopt(zmq.RCVTIMEO, int(float(os.environ["WAIT"]) * 1000))
socket.connect("tcp://127.0.0.1:%d" % int(os.environ["PORT"]))
try:
    socket.send_string(json.dumps({"op": os.environ["OP"]}))
    print(socket.recv_string())
except zmq.Again:
    print(json.dumps({"ok": False, "reason": "no_reply", "detail": "the camera service did not answer on its mission socket"}))
    sys.exit(6)
PYSRC
"$PYBIN" "$T"
ASK
  return "$rc"
}

lock_status() {
  # shellcheck disable=SC2029  # REMOTE_PYTHON and REMOTE_DIR are built in this script
  "${SSH[@]}" "PYBIN=$REMOTE_PYTHON DIR='$REMOTE_DIR' bash -s" <<'LOCK'
set -uo pipefail
cd "$DIR" || { echo "REFUSE: the camera service is not deployed on the robot computer" >&2; exit 3; }
"$PYBIN" bin/pi/camera_service.py --lock-status
LOCK
}

# True when <role> is held by <label>, from a lock-status document on stdin.
lock_held_by() {
  python3 -c 'import json,sys
try:
    status = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
entry = (status.get("roles") or {}).get(sys.argv[1]) or {}
sys.exit(0 if entry.get("held") and (entry.get("holder") or {}).get("holder") == sys.argv[2] else 1)' "$1" "$2"
}

# The holder label and process id of every held role, one per line, from a lock-status document on stdin.
held_holders() {
  python3 -c 'import json,sys
try:
    status = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for role, entry in sorted((status.get("roles") or {}).items()):
    if entry.get("held"):
        holder = entry.get("holder") or {}
        print("%s: %s (pid %s)" % (role, holder.get("holder", "unknown"), holder.get("pid", "unknown")))'
}

case "$ACTION" in
  lock-status)
    deploy
    lock_status
    ;;

  status)
    rc=0; ask status 5 || rc=$?
    [ "$rc" -eq 0 ] || { echo "REFUSE: no camera service answered on the robot computer (status $rc)" >&2; exit 6; }
    ;;

  stop)
    echo "== stop: SIGTERM, then wait for every device to be closed and every lock released"
    # shellcheck disable=SC2029  # constants of this script only
    "${SSH[@]}" 'bash -s' <<'STOP'
set -uo pipefail
pids="$(pgrep -f 'camera_service[.]py' || true)"
if [ -z "$pids" ]; then echo "no camera service was running"; exit 0; fi
# shellcheck disable=SC2086  # pgrep gives a whitespace-separated list of numbers
kill -TERM $pids
for _ in $(seq 1 20); do
  sleep 1
  pgrep -f 'camera_service[.]py' >/dev/null 2>&1 || { echo "the camera service stopped"; exit 0; }
done
echo "REFUSE: the camera service did not stop within 20 s; it still owns its cameras"
exit 3
STOP
    echo "-- locks after the stop:"
    lock_status
    ;;

  start)
    echo "== 1/4 preflight (read-only): no service, no camera owner, no held lock"
    # shellcheck disable=SC2029  # constants of this script only
    "${SSH[@]}" 'bash -s' <<'PRE'
set -uo pipefail
if pgrep -f 'camera_service[.]py' >/dev/null 2>&1; then echo "REFUSE: a camera service is already running on the robot computer"; exit 3; fi
for n in /dev/video*; do
  [ -e "$n" ] || continue
  if fuser "$n" >/dev/null 2>&1; then echo "REFUSE: $n already has an owner, and that owner does not take the camera lock (a viewer, or a host configured with cameras)"; exit 3; fi
done
echo "preflight ok: no service, no video node has an owner"
PRE
    echo "== 2/4 deploy"
    deploy
    echo "== 3/4 the camera locks before the start"
    HELD="$(lock_status)"
    printf '%s\n' "$HELD"
    HOLDERS="$(printf '%s\n' "$HELD" | held_holders)"
    if [ -n "$HOLDERS" ]; then
      echo "REFUSE: a camera lock is already held, so the capture owner is somebody else and this service must not start:" >&2
      printf '  %s\n' "$HOLDERS" >&2
      exit 3
    fi
    if [ "$FRONT" -eq 1 ]; then echo "== 4/4 start, detached on the robot computer: wrist AND front"; else echo "== 4/4 start, detached on the robot computer: wrist only"; fi
    [ "$FRONT" -eq 0 ] || echo "   WARNING: --enable-front: the front camera is NOT QUALIFIED (docs/autonomous-demo/camera-service.md step 4). Its prerequisite is unproven; it has NOT been shown to cause harm. The earlier instability attribution is withdrawn."
    TS=$(date -u +%Y%m%dT%H%M%SZ)
    IDENTITIES="KC_WRIST_CAMERA_ID='$KC_WRIST_CAMERA_ID'"
    OPTS="$SERVICE_OPTS"
    if [ "$FRONT" -eq 1 ]; then IDENTITIES="$IDENTITIES KC_FRONT_CAMERA_ID='$KC_FRONT_CAMERA_ID'"; OPTS="$OPTS --enable-front"; fi
    # shellcheck disable=SC2029  # the identities are validated above and are never printed; OPTS holds validated numbers only
    "${SSH[@]}" "PYBIN=$REMOTE_PYTHON DIR='$REMOTE_DIR' LOG='kc-camera-service/logs/$TS.log' OPTS='$OPTS' $IDENTITIES bash -s" <<'START'
set -uo pipefail
cd "$HOME" || exit 3
mkdir -p kc-camera-service/logs
cd "$DIR" || { echo "REFUSE: the camera service is not deployed"; exit 3; }
# shellcheck disable=SC2086  # OPTS holds validated option words that must split
setsid nohup "$PYBIN" -u bin/pi/camera_service.py $OPTS >"$HOME/$LOG" 2>&1 </dev/null &
echo "started, detached; its output goes to $LOG under the remote account's home"
START
    echo "-- waiting for the service to own the wrist camera (up to 25 s)"
    READY=0
    for _ in $(seq 1 25); do
      sleep 1
      if ask ping 2 >/dev/null 2>&1; then READY=1; break; fi
    done
    if [ "$READY" -ne 1 ]; then
      echo "REFUSE: the camera service did not answer within 25 s. The last lines of its log:" >&2
      # shellcheck disable=SC2029  # LOG is built here from a UTC timestamp
      "${SSH[@]}" "tail -n 20 'kc-camera-service/logs/$TS.log' 2>/dev/null || echo '(no log)'" >&2
      exit 3
    fi
    echo "-- the camera locks after the start (the wrist must be held by camera_service):"
    HELD="$(lock_status)"
    printf '%s\n' "$HELD"
    if ! printf '%s\n' "$HELD" | lock_held_by wrist camera_service; then
      echo "REFUSE: the service answered but it does not hold the wrist camera lock; it is not the capture owner. Its log is under kc-camera-service/logs on the robot computer." >&2
      exit 3
    fi
    echo "-- service status:"
    ask status 5
    ;;
esac
