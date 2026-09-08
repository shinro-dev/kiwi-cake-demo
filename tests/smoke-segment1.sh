#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tests/smoke-segment1.sh: run segment 1 and assert every step marker and the
# PASS line. This is the acceptance test a LeKiwi owner runs cold before ever
# considering segment 2. Arguments are passed through to bin/demo-segment1.sh
# (for example --unsupported-target on a board that is not the tested Pi 5).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mktemp "${TMPDIR:-/tmp}/kc-smoke.XXXXXX")"; trap 'rm -f -- "$OUT"' EXIT
"$ROOT/bin/demo-segment1.sh" "$@" 2>&1 | tee "$OUT"
RC="${PIPESTATUS[0]}"
FAILS=0
[ "$RC" -eq 0 ] || { echo "smoke: the runner exited $RC"; FAILS=$((FAILS + 1)); }
i=0
for step in doctor keys build verify preflight configuration tamper start telemetry child-kill resident-kill clean-stop summary; do
  i=$((i + 1))
  grep -qx "KIWI-CAKE step $i/13 $step: ok" "$OUT" || { echo "smoke: missing marker for step $i $step"; FAILS=$((FAILS + 1)); }
done
for expect in 'verdict.*admitted\|signed capsule under require, your key trusted: admitted' 'unsigned twin under require: refused signature_missing' 'signed capsule with only the control key trusted: refused signature_key_id_not_trusted' 'store-object-mismatch' 'poll queries answered 3 of 3' 'EVT_SUPERVISOR_CHILD_EXITED .*(signal 9)' 'EVT_SUPERVISOR_CHILD_STARTED ordinal=' 'no child of the killed resident survives' 'session_uuid .* -> .* (fresh)' 'cake-resident: stopped; socket removed; no child' 'KIWI-CAKE SEGMENT 1: PASS'; do
  grep -q "$expect" "$OUT" || { echo "smoke: expected output not found: $expect"; FAILS=$((FAILS + 1)); }
done
[ "$FAILS" -eq 0 ] && echo "smoke-segment1: PASS" || { echo "smoke-segment1: $FAILS failure(s)"; exit 1; }
