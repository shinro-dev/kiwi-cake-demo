#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/run-all.sh: every test of the tooling itself (not the board smoke test).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
for t in test-docs test-version test-python test-preflight-classify test-gate test-templates test-verify test-walkthrough test-segment1-mock; do
  echo "=== $t ==="
  if "$ROOT/tests/$t.sh"; then :; else rc=$?; [ "$rc" -eq 75 ] && echo "(skipped: precondition missing)" || FAILS=$((FAILS + 1)); fi
done
[ "$FAILS" -eq 0 ] && echo "run-all: PASS" || { echo "run-all: $FAILS suite(s) failed"; exit 1; }
