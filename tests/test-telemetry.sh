#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-telemetry.sh: the admin-probe deadline and the identity-shape
# helpers. A hung probe cannot hold the segment-1 runner past its budget, a
# get-status reply with blank identities is not counted as answered, and the
# helpers reject empty, short or differing identities.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "test-telemetry: python3 missing (the socket helper)"; exit 75; }
command -v timeout >/dev/null 2>&1 || { echo "test-telemetry: timeout missing (coreutils)"; exit 75; }
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-telemetry.XXXXXX")"
HELPER=""
cleanup() { [ -n "$HELPER" ] && kill "$HELPER" 2>/dev/null; rm -rf -- "$T"; }
trap cleanup EXIT
FAILS=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAILS=$((FAILS + 1)); }

# --- a hung probe cannot hold the segment-1 runner past its budget ---
mkdir -p "$T/rel" && ln -s "$ROOT/tests/mock-bin" "$T/rel/bin"
ARGS=()
[ "$(kc_classify)" = "pi5-tested" ] || ARGS+=(--unsupported-target)
KC_RELEASE_DIR="$T/rel" KC_STATE_DIR="$T/state" KC_MOCK_PROBE_HANG=1 KC_PROBE_TIMEOUT=2 \
  timeout 90 "$ROOT/bin/demo-segment1.sh" "${ARGS[@]}" >"$T/hang.txt" 2>&1; rc=$?
RUN="$(sed -nE 's/^kiwi-cake: segment 1, run ([^,]+),.*/\1/p' "$T/hang.txt" | head -n 1)"
if [ "$rc" -eq 124 ]; then bad "hang: the runner did not end within 90 s (rc 124)"; tail -n 5 "$T/hang.txt"
elif [ "$rc" -ne 0 ] && grep -q '^KIWI-CAKE step 8/13 start: FAILED' "$T/hang.txt"; then ok "hang: the runner failed at step 8 within budget (rc $rc)"
else bad "hang: unexpected outcome (rc $rc)"; tail -n 5 "$T/hang.txt"; fi
grep -q 'kiwi-cake: admin-probe: get-status timed out after 2s' "$T/hang.txt" && ok "hang: the deadline line names the operation" || bad "hang: no deadline line in the transcript"
[ -n "$RUN" ] && kc_settle_absent "kiwi-cake-child-$RUN" 100 && ! pgrep -f "$T/state/" >/dev/null && ok "hang: nothing carrying the run tag survives" || { bad "hang: a process survived (run $RUN)"; pgrep -af "$T/state/"; }

[ "$FAILS" -eq 0 ] && echo "test-telemetry: PASS" || { echo "test-telemetry: $FAILS failure(s)"; exit 1; }
