#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-preflight-classify.sh: the preflight classifier over recorded
# transcripts, at the exit statuses demo-preflight can and cannot produce.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
F="$ROOT/tests/fixtures"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-classify.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
expect() {
  # expect LABEL OUT ERR RC WANT
  local label="$1" out="$2" err="$3" rc="$4" want="$5" got
  got="$(kc_classify_preflight "$out" "$err" "$rc")"
  if [ "$got" = "$want" ]; then echo "ok   $label rc=$rc -> $got"; else echo "FAIL $label rc=$rc -> $got (want $want)"; FAILS=$((FAILS + 1)); fi
}
fixture() { expect "$1" "$F/$1.out" "$F/$1.err" "$2" "$3"; }
G="$F/preflight-five-green-observer"
OBSERVER='demo-preflight: REFUSED: check observer: --observer-module was not given, so the module this board builds for itself would first be exercised by the resident; that is the divergence this check exists to close'
# The four recorded transcripts at the status demo-preflight really returns.
fixture preflight-five-green-observer 1 five-green-observer-refused
fixture preflight-page-size 1 'refused:page-size:page size 4096, the tested board record expects 16384'
fixture preflight-glibc 1 "refused:glibc:glibc 2.36, below the tested board's 2.41 or below the target profile's libc_min 2.34"
fixture preflight-observer-without-green 1 "refused:observer:${OBSERVER#demo-preflight: REFUSED: check observer: }"
# A PASS line with rc 0 is a pass; rc 0 without it is not.
printf 'demo-preflight: PASS\n' >"$T/pass.out"; : >"$T/empty.err"
expect pass "$T/pass.out" "$T/empty.err" 0 pass
expect preflight-five-green-observer "$G.out" "$G.err" 0 unparsed:no-pass-line
# Any status other than 0 or 1 reached no verdict, whatever was printed.
expect preflight-five-green-observer "$G.out" "$G.err" 139 unparsed:abnormal-exit:139
expect preflight-five-green-observer "$G.out" "$G.err" 2 unparsed:abnormal-exit:2
expect preflight-five-green-observer "$G.out" "$G.err" '' 'unparsed:abnormal-exit:'
# Two refusals cannot come from one run.
cat "$G.err" "$F/preflight-page-size.err" >"$T/two.err"
expect observer-plus-page-size "$G.out" "$T/two.err" 1 unparsed:contradictory-refusals
# The observer refusal is accepted only as the exact known line.
printf '%s junk\n' "$OBSERVER" >"$T/junk.err"
expect observer-with-junk "$G.out" "$T/junk.err" 1 "refused:observer:${OBSERVER#demo-preflight: REFUSED: check observer: } junk"
# rc 1 with no refusal line is not a refusal.
expect five-green-no-refusal "$G.out" "$T/empty.err" 1 unparsed:no-refusal-line
# The explanations name the check or the reason.
kc_explain_refusal page-size "page size 4096, the tested board record expects 16384" | grep -q 'kernel=kernel8.img' && echo "ok   explanation mentions the kernel" || { echo "FAIL explanation"; FAILS=$((FAILS + 1)); }
kc_explain_unparsed unparsed:abnormal-exit:139 2>/dev/null | grep -q 'died on signal 11' && echo "ok   abnormal-exit explanation names signal 11" || { echo "FAIL abnormal-exit explanation"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-preflight-classify: PASS" || { echo "test-preflight-classify: $FAILS failure(s)"; exit 1; }
