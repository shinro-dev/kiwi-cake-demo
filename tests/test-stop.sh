#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-stop.sh: bin/demo-stop.sh against a fake systemctl and planted
# processes. It stops what is there, reports it in the documented words, and
# fails when a stop does not complete or a process outlives the stop.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-stop.XXXXXX")"
BG=()
cleanup() { local p; for p in "${BG[@]}"; do pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; rm -rf -- "$T"; }
trap cleanup EXIT
FAILS=0
UNIT="kiwi-cake-demo.service"
mkdir -p "$T/tree" "$T/bin" "$T/state" "$T/run" "$T/host"
# The script derives its own directory, so the operator's run-child.sh is planted next to the copy.
cp -r "$ROOT/bin" "$T/tree/bin" && cp "$ROOT/VERSION" "$T/tree/VERSION" || { echo "test-stop: cannot copy the tree"; exit 75; }
STOP="$T/tree/bin/demo-stop.sh"
cat >"$T/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
# fake systemctl for tests/test-stop.sh: the unit state lives in KC_FAKE_STATE_FILE
[ "${1:-}" = "--user" ] && shift
F="$KC_FAKE_STATE_FILE"
[ -f "$F" ] || printf '%s\n' "${KC_FAKE_UNIT_STATE:-absent}" >"$F"
state="$(cat "$F")"
case "${1:-}" in
  list-unit-files) [ "$state" != "absent" ] ;;
  is-active) [ "${2:-}" = "--quiet" ] || echo "$state"; [ "$state" = "active" ] ;;
  stop) [ "${KC_FAKE_STOP_RC:-0}" -eq 0 ] || exit 1; [ "${KC_FAKE_STOP_HANGS:-0}" -eq 1 ] || echo inactive >"$F"; exit 0 ;;
  show) echo ;;
  *) exit 0 ;;
esac
EOF
chmod 755 "$T/bin/systemctl"
# run_stop STATE_FILE SEED [VAR=VALUE ...]: output in $T/out, status in RC.
run_stop() {
  local f="$1" seed="$2"; shift 2
  env "$@" PATH="$T/bin:$PATH" KC_STATE_DIR="$T/state" XDG_RUNTIME_DIR="$T/run" KC_FAKE_STATE_FILE="$T/$f" KC_FAKE_UNIT_STATE="$seed" "$STOP" >"$T/out" 2>&1
  RC=$?
}
# check WHAT RC_WANTED LINE...: the run exited as wanted and printed every line verbatim.
check() {
  local what="$1" want="$2" ok=1 l; shift 2
  [ "$RC" -eq "$want" ] || ok=0
  for l in "$@"; do grep -qF -- "$l" "$T/out" || ok=0; done
  if [ "$ok" -eq 1 ]; then echo "ok   $what"; else echo "FAIL $what (rc $RC, want $want); output follows"; cat "$T/out"; FAILS=$((FAILS + 1)); fi
}
MOTOR="kiwi-cake: motor state is your host's own disconnect behaviour and is not visible to this script; check the robot"
DONE0="kiwi-cake: done (0 stop action(s)): no resident, no supervised child, no socket"
LEFT1="kiwi-cake: FAIL: 1 process(es) still in the process table after the stop"
# 1. no unit, nothing running
run_stop unit1 absent
check "missing unit, nothing running: exit 0, 0 actions" 0 "$MOTOR" "$DONE0"
grep -q "$UNIT" "$T/out" && { echo "FAIL a unit that is not installed was mentioned"; FAILS=$((FAILS + 1)); }
# 2. active unit, stop succeeds; 6. the same unit stopped again
run_stop unit2 active
check "active unit, stop ok: exit 0, 1 action" 0 "kiwi-cake: stopped $UNIT" "$MOTOR" "kiwi-cake: done (1 stop action(s)): no resident, no supervised child, no socket"
run_stop unit2 active
check "repeated stop: installed and not active, 0 actions" 0 "kiwi-cake: $UNIT is installed and not active" "$DONE0"
# 3. stop exits 1
run_stop unit3 active KC_FAKE_STOP_RC=1
check "stop rc 1 is a failure" 1 "kiwi-cake: FAIL: systemctl --user stop $UNIT failed"
# 4. stop returns but the unit never leaves active
run_stop unit4 active KC_FAKE_STOP_HANGS=1 KC_STOP_DEADLINE=1
check "stop that never settles fails at the deadline" 1 "$MOTOR" "kiwi-cake: FAIL: $UNIT is still active after 1 s"
# 5. a host-shaped process named by the operator's run-child.sh outlives the stop
printf '#!/bin/sh\nsleep 300; true\n' >"$T/host/lekiwi_host_noninteractive.py"
printf '#!/bin/bash\n# filled wrapper for the test\nexec /usr/bin/python3 %s \\\n  --robot.id=kiwi\n' "$T/host/lekiwi_host_noninteractive.py" >"$T/tree/bin/run-child.sh"
sh "$T/host/lekiwi_host_noninteractive.py" &
HOST=$!; BG+=("$HOST")
run_stop unit5 inactive
check "a host process outliving the stop fails and is named" 1 "still alive: $HOST " "$LEFT1"
pkill -P "$HOST" 2>/dev/null; kill "$HOST" 2>/dev/null; wait "$HOST" 2>/dev/null; rm -f "$T/tree/bin/run-child.sh"
# 7. a segment-1 stub carrying the run tag outlives the stop
sh -c 'sleep 300; true' kiwi-cake-child-test &
STUB=$!; BG+=("$STUB")
run_stop unit7 absent
check "a tagged stub outliving the stop fails and is named" 1 "still alive: $STUB " "$LEFT1"
pkill -P "$STUB" 2>/dev/null; kill "$STUB" 2>/dev/null; wait "$STUB" 2>/dev/null
# 8. arguments
"$STOP" --help >/dev/null 2>&1 && echo "ok   --help exits 0" || { echo "FAIL --help"; FAILS=$((FAILS + 1)); }
"$STOP" --bogus >/dev/null 2>&1; [ $? -eq 5 ] && echo "ok   an unknown argument exits 5" || { echo "FAIL unknown argument"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-stop: PASS" || { echo "test-stop: $FAILS failure(s)"; exit 1; }
