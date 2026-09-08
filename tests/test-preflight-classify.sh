#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-preflight-classify.sh: the preflight classifier over recorded transcripts.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
F="$ROOT/tests/fixtures"
FAILS=0
expect() {
  local name="$1" rc="$2" want="$3" got
  got="$(kc_classify_preflight "$F/$name.out" "$F/$name.err" "$rc")"
  if [ "$got" = "$want" ]; then echo "ok   $name -> $got"; else echo "FAIL $name -> $got (want $want)"; FAILS=$((FAILS + 1)); fi
}
expect preflight-five-green-observer 1 five-green-observer-refused
expect preflight-page-size 1 'refused:page-size:page size 4096, the tested board record expects 16384'
expect preflight-glibc 1 "refused:glibc:glibc 2.36, below the tested board's 2.41 or below the target profile's libc_min 2.34"
expect preflight-observer-without-green 1 'refused:observer:--observer-module was not given, so the module this board builds for itself would first be exercised by the resident; that is the divergence this check exists to close'
# A pass line with rc 0 is a pass.
printf 'demo-preflight: PASS\n' >"$F/.pass.out"; : >"$F/.pass.err"
got="$(kc_classify_preflight "$F/.pass.out" "$F/.pass.err" 0)"; rm -f "$F/.pass.out" "$F/.pass.err"
if [ "$got" = "pass" ]; then echo "ok   pass -> pass"; else echo "FAIL pass -> $got"; FAILS=$((FAILS + 1)); fi
# The explanation names the check.
kc_explain_refusal page-size "page size 4096, the tested board record expects 16384" | grep -q 'kernel=kernel8.img' && echo "ok   explanation mentions the kernel" || { echo "FAIL explanation"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-preflight-classify: PASS" || { echo "test-preflight-classify: $FAILS failure(s)"; exit 1; }
