#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/run-all.sh: every test of the tooling itself, in a fixed order, each
# with a kind (documents, pure-functions, fakes, real-tools, mock-binaries).
# A suite exits 0 (PASS; any 'SKIP <check>: <reason>' line is a check it could
# not perform), 75 (SKIPPED; its last line must be '<suite>: <reason>') or
# anything else (FAIL). After the suites: one 'run-all: <suite> <kind> <status>'
# line per suite, every collected SKIP line, then one count line; exit 1 iff a
# suite failed. run-all never runs the board tests tests/smoke-segment1.sh and
# tests/unit-stop-signals.sh: those need the tested Raspberry Pi 5 with Cake.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-run-all.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
ROSTER=(
  'test-docs documents'
  'test-version pure-functions'
  'test-python real-tools'
  'test-preflight-classify pure-functions'
  'test-doctor fakes'
  'test-gate real-tools'
  'test-templates pure-functions'
  'test-verify real-tools'
  'test-walkthrough documents'
  'test-telemetry fakes'
  'test-ports pure-functions'
  'test-stop fakes'
  'test-journal fakes'
  'test-segment1-mock mock-binaries'
  'test-archive mock-binaries'
)
PASSED=0; FAILED=0; SKIPPED=0; CHECKS=0
TABLE=()
: >"$T/skips"
for entry in "${ROSTER[@]}"; do
  suite="${entry%% *}"; kind="${entry#* }"; out="$T/$suite.out"
  echo "=== $suite ==="
  if [ -x "$ROOT/tests/$suite.sh" ]; then
    "$ROOT/tests/$suite.sh" 2>&1 | tee "$out"; rc="${PIPESTATUS[0]}"
  else
    echo "$suite: tests/$suite.sh is missing or not executable" | tee "$out"; rc=127
  fi
  n="$(grep -c '^SKIP ' "$out")"; CHECKS=$((CHECKS + n))
  grep '^SKIP ' "$out" >>"$T/skips"
  case "$rc" in
    0) if [ "$n" -eq 0 ]; then status="PASS"; else status="PASS with $n skipped check(s)"; fi; PASSED=$((PASSED + 1)) ;;
    75) last="$(tail -n 1 "$out")"
        if [ "${last#"$suite: "}" != "$last" ]; then status="SKIPPED (${last#"$suite: "})"; SKIPPED=$((SKIPPED + 1))
        else status="FAIL (exit 75 without a reason line)"; FAILED=$((FAILED + 1)); fi ;;
    *) status="FAIL (exit $rc)"; FAILED=$((FAILED + 1)) ;;
  esac
  TABLE+=("run-all: $suite $kind $status")
done
printf '%s\n' "${TABLE[@]}"
if [ -s "$T/skips" ]; then echo "run-all: skipped checks:"; cat "$T/skips"; else echo "run-all: skipped checks: none"; fi
echo "run-all: $PASSED passed, $FAILED failed, $SKIPPED suites skipped, $CHECKS checks skipped"
[ "$FAILED" -eq 0 ] || exit 1
