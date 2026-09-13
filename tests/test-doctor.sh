#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-doctor.sh: bin/doctor.sh --capsule turns the preflight verdict
# into its exit status (6 refused, 7 unparsed, both ahead of 3), run against
# the mock binaries and a demo-preflight that replays a recorded transcript.
# It proves the doctor, not Cake.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
F="$ROOT/tests/fixtures"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-doctor.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
mkdir -p "$T/rel/bin" "$T/scratch"
for b in cake-resident admin-probe demo-plan; do ln -s "$ROOT/tests/mock-bin/$b" "$T/rel/bin/$b"; done
cat >"$T/rel/bin/demo-preflight" <<'FAKE'
#!/usr/bin/env bash
# Replays the transcript named by KC_TEST_PREFLIGHT_FIXTURE, exits KC_TEST_PREFLIGHT_RC.
set -uo pipefail
if printf '%s\n' "$@" | grep -qx -- '--list-checks'; then
  printf 'page-size\nglibc\nmemfd-noexec\nadmission\nload-probe\nobserver\nshipped-binaries\n'; exit 0
fi
[ $# -gt 0 ] || { echo "demo-preflight: REFUSED: --capsule is required" >&2; exit 1; }
cat "$KC_TEST_PREFLIGHT_FIXTURE.out"
cat "$KC_TEST_PREFLIGHT_FIXTURE.err" >&2
exit "$KC_TEST_PREFLIGHT_RC"
FAKE
chmod 755 "$T/rel/bin/demo-preflight"
doctor() {
  # doctor FIXTURE RC: the doctor against the replayed transcript; output in $T/out, status in RC.
  KC_RELEASE_DIR="$T/rel" KC_TEST_PREFLIGHT_FIXTURE="$F/$1" KC_TEST_PREFLIGHT_RC="$2" \
    "$ROOT/bin/doctor.sh" --capsule /dev/null --trusted-key 00 --store-scratch "$T/scratch" >"$T/out" 2>&1
  RC=$?
}
doctor preflight-five-green-observer 1
grep -q 'Accepted\.' "$T/out" && echo "ok   five green then observer: Accepted." || { echo "FAIL five green then observer: no 'Accepted.' line"; FAILS=$((FAILS + 1)); }
case "$RC" in 0 | 3) echo "ok   five green then observer exits $RC" ;; *) echo "FAIL five green then observer exits $RC (want 0 or 3)"; FAILS=$((FAILS + 1)) ;; esac
doctor preflight-page-size 1
grep -qx 'Preflight verdict: REFUSED\.' "$T/out" && echo "ok   page-size refusal: REFUSED line" || { echo "FAIL page-size refusal: no 'Preflight verdict: REFUSED.' line"; FAILS=$((FAILS + 1)); }
[ "$RC" -eq 6 ] && echo "ok   page-size refusal exits 6" || { echo "FAIL page-size refusal exits $RC (want 6)"; FAILS=$((FAILS + 1)); }
doctor preflight-five-green-observer 139
grep -q 'could not be parsed' "$T/out" && echo "ok   exit 139: could not be parsed" || { echo "FAIL exit 139: no 'could not be parsed' line"; FAILS=$((FAILS + 1)); }
[ "$RC" -eq 7 ] && echo "ok   exit 139 exits 7" || { echo "FAIL exit 139 exits $RC (want 7)"; FAILS=$((FAILS + 1)); }
# docs/targets.md and the doctor's header list every exit code.
for c in 0 3 4 5 6 7; do
  grep -qE "^\| $c \| " "$ROOT/docs/targets.md" && echo "ok   docs/targets.md has a row for exit $c" || { echo "FAIL docs/targets.md has no row for exit $c"; FAILS=$((FAILS + 1)); }
done
grep -q '6 demo-preflight refused at a named check' "$ROOT/bin/doctor.sh" && grep -q '7 demo-preflight output could not be parsed' "$ROOT/bin/doctor.sh" && echo "ok   doctor header lists 6 and 7" || { echo "FAIL doctor header lists 6 and 7"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-doctor: PASS" || { echo "test-doctor: $FAILS failure(s)"; exit 1; }
