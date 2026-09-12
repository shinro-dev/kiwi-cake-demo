#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-segment1-mock.sh: the smoke test against the mock binaries in
# tests/mock-bin, so the runner's own logic is exercised on any Linux host.
# It proves the runner, not Cake.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "test-segment1-mock: python3 missing (the mock socket helper)"; exit 75; }
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-mock.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
mkdir -p "$T/rel" && ln -s "$ROOT/tests/mock-bin" "$T/rel/bin"
ARGS=()
# The mocks run on any board; on one that is not the tested Pi 5 the runner needs the override.
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
[ "$(kc_classify)" = "pi5-tested" ] || ARGS+=(--unsupported-target)
KC_RELEASE_DIR="$T/rel" KC_STATE_DIR="$T/state" "$ROOT/tests/smoke-segment1.sh" "${ARGS[@]}" >"$T/out.txt" 2>&1
RC=$?
tail -n 3 "$T/out.txt"
FAILS=0
[ "$RC" -eq 0 ] && echo "ok   plain run: smoke PASS" || { echo "FAIL plain run (rc $RC); transcript follows"; cat "$T/out.txt"; FAILS=$((FAILS + 1)); }

# --pause with twelve newlines on stdin: every pause is honoured and the run still passes.
printf '\n%.0s' $(seq 1 12) | KC_RELEASE_DIR="$T/rel" KC_STATE_DIR="$T/state2" "$ROOT/tests/smoke-segment1.sh" "${ARGS[@]}" --pause >"$T/out2.txt" 2>&1
RC=$?
NP="$(grep -c '^PAUSE: press Enter for step ' "$T/out2.txt")"
if [ "$RC" -eq 0 ] && [ "$NP" -eq 12 ] && grep -q '^KIWI-CAKE SEGMENT 1: PASS$' "$T/out2.txt"; then echo "ok   --pause: 12 pauses honoured, run PASS"; else echo "FAIL --pause with newlines (rc $RC, pauses $NP)"; tail -n 5 "$T/out2.txt"; FAILS=$((FAILS + 1)); fi

# --pause with a closed stdin: refused at the first pause with exit 5, nothing left behind.
KC_RELEASE_DIR="$T/rel" KC_STATE_DIR="$T/state3" "$ROOT/bin/demo-segment1.sh" "${ARGS[@]}" --pause >"$T/out3.txt" 2>&1 </dev/null
RC=$?
RUN3="$(sed -nE 's/^kiwi-cake: segment 1, run ([^,]+),.*/\1/p' "$T/out3.txt" | head -n 1)"
if [ "$RC" -eq 5 ] && grep -q -- '--pause needs a readable standard input' "$T/out3.txt" && [ "$(grep -c '^KIWI-CAKE step .*: ok$' "$T/out3.txt")" -eq 1 ]; then echo "ok   --pause with closed stdin: refused at the first pause (5)"; else echo "FAIL --pause with closed stdin (rc $RC)"; tail -n 5 "$T/out3.txt"; FAILS=$((FAILS + 1)); fi
[ -n "$RUN3" ] && [ -z "$(kc_pids_carrying "kiwi-cake-child-$RUN3")" ] && ! pgrep -f "$T/state3/" >/dev/null && echo "ok   closed-stdin refusal left no process behind" || { echo "FAIL closed-stdin refusal left a process (run $RUN3)"; FAILS=$((FAILS + 1)); }

# Ctrl-C at a pause while the resident and the stub are alive: the trap stops both and exits 130.
# Standard input is a FIFO this test keeps open: eight newlines pass the first eight pauses,
# then the ninth read blocks with the resident and the stub alive. Job control is enabled for
# the launch: without it a non-interactive bash starts background jobs with SIGINT ignored,
# and a signal ignored at entry cannot be trapped by the runner.
mkfifo "$T/stdin4"; exec 3<>"$T/stdin4"; printf '\n%.0s' $(seq 1 8) >&3
set -m
KC_RELEASE_DIR="$T/rel" KC_STATE_DIR="$T/state4" "$ROOT/bin/demo-segment1.sh" "${ARGS[@]}" --pause <"$T/stdin4" >"$T/out4.txt" 2>&1 &
PID4=$!
set +m
for _ in $(seq 1 600); do grep -q '^PAUSE: press Enter for step 10/13' "$T/out4.txt" 2>/dev/null && break; sleep 0.1; done
RUN4="$(sed -nE 's/^kiwi-cake: segment 1, run ([^,]+),.*/\1/p' "$T/out4.txt" | head -n 1)"
ALIVE_BEFORE="$(kc_pids_carrying "kiwi-cake-child-$RUN4" | wc -w)"
kill -INT "$PID4"
wait "$PID4" 2>/dev/null; RC=$?
exec 3>&-
if grep -q '^PAUSE: press Enter for step 10/13' "$T/out4.txt" && [ "$ALIVE_BEFORE" -ge 1 ]; then echo "ok   Ctrl-C case reached pause 9 with a live child ($ALIVE_BEFORE process(es) carrying the tag)"; else echo "FAIL Ctrl-C case did not reach pause 9 with a live child"; tail -n 5 "$T/out4.txt"; FAILS=$((FAILS + 1)); fi
[ "$RC" -eq 130 ] && grep -q '^interrupted: stopping the resident and the child' "$T/out4.txt" && echo "ok   Ctrl-C at a pause exits 130 through the trap" || { echo "FAIL Ctrl-C at a pause: rc $RC"; tail -n 5 "$T/out4.txt"; FAILS=$((FAILS + 1)); }
kc_settle_absent "kiwi-cake-child-$RUN4" 100 && ! pgrep -f "$T/state4/" >/dev/null && echo "ok   the trap stopped the resident and the stub" || { echo "FAIL a process survived the Ctrl-C (run $RUN4)"; pgrep -af "$T/state4/"; FAILS=$((FAILS + 1)); }

[ "$FAILS" -eq 0 ] && echo "test-segment1-mock: PASS" || { echo "test-segment1-mock: $FAILS failure(s)"; exit 1; }
