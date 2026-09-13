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

# --- the helpers ---
H64A="$(printf '%064d' 0 | tr 0 a)"; H64B="$(printf '%064d' 0 | tr 0 b)"; H32="$(printf '%032d' 0 | tr 0 c)"
GOOD="status ok
build_identity $H64A
session_uuid $H32
plan_digest $H64A
config_identity $H64A
target_profile_digest $H64A
epoch 2"
got="$(kc_identities_match "" "" plan_digest)"; rc=$?
[ "$rc" -eq 1 ] && [ "$got" = "identity plan_digest: missing or malformed before" ] && ok "identities: both empty fail naming the field" || bad "identities: both empty (rc $rc: $got)"
kc_identities_match "$GOOD" "$GOOD" plan_digest config_identity build_identity target_profile_digest >/dev/null && ok "identities: well-formed and equal pass" || bad "identities: well-formed and equal"
OTHER="${GOOD/plan_digest $H64A/plan_digest $H64B}"
got="$(kc_identities_match "$GOOD" "$OTHER" plan_digest)"; rc=$?
[ "$rc" -eq 1 ] && [ "$got" = "identity plan_digest: $H64A before, $H64B after" ] && ok "identities: one side different fails" || bad "identities: one side different (rc $rc: $got)"
SHORT="${GOOD/session_uuid $H32/session_uuid ${H32%c}}"
got="$(kc_identities_match "$SHORT" "$GOOD" session_uuid)"; rc=$?
[ "$rc" -eq 1 ] && [ "$got" = "identity session_uuid: missing or malformed before" ] && ok "identities: session_uuid of 31 characters fails" || bad "identities: session_uuid 31 (rc $rc: $got)"
kc_status_fields_ok "$GOOD" >/dev/null && ok "status fields: a complete reply passes" || bad "status fields: complete reply"
got="$(kc_status_fields_ok "$(printf '%s\n' "$GOOD" | grep -v '^epoch ')")"; rc=$?
[ "$rc" -eq 1 ] && [ "$got" = "missing or malformed: epoch" ] && ok "status fields: a missing epoch is named" || bad "status fields: missing epoch (rc $rc: $got)"
kc_hex_field "build_identity $(printf '%064d' 0 | tr 0 A)" build_identity 64 >/dev/null && bad "hex field: uppercase accepted" || ok "hex field: uppercase rejected"

# --- a socket and a state directory the mock admin-probe accepts ---
SOCK="$T/admin.sock"; STATE="$SOCK.state"; mkdir -p "$STATE"
python3 - "$SOCK" <<'PY' &
import socket, sys, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.bind(sys.argv[1]); s.listen(1)
while True: time.sleep(1)
PY
HELPER=$!
for _ in $(seq 1 100); do [ -S "$SOCK" ] && break; sleep 0.05; done
[ -S "$SOCK" ] || { echo "test-telemetry: the socket helper did not bind"; exit 75; }
{
  echo "build_identity $H64A"; echo "session_uuid $H32"; echo "target_profile_digest $H64A"
  echo "plan_digest $H64A"; echo "epoch 2"; echo "slots 1"; echo "bindings 0"; echo "resources 1"
  echo "config_identity $H64A"; echo "transaction_state none"; echo "flight_sequence_oldest 0"
} >"$STATE/status"
printf 'CAKEPLN mock\ncapsule %s\nplan_digest %s\n' "$H64A" "$H64A" >"$STATE/plan"
: >"$STATE/flight"
PROBE="$ROOT/tests/mock-bin/admin-probe"
"$ROOT/bin/telemetry.sh" --probe "$PROBE" --socket "$SOCK" --rounds 1 >"$T/full.txt" 2>&1; rc=$?
[ "$rc" -eq 0 ] && grep -qx 'poll queries answered 3 of 3' "$T/full.txt" && grep -qx 'health_surface not served (status unavailable)' "$T/full.txt" && ok "telemetry: a complete reply answers 3 of 3, exit 0" || { bad "telemetry: complete reply (rc $rc)"; cat "$T/full.txt"; }
KC_MOCK_PROBE_BLANK_FIELDS=1 "$ROOT/bin/telemetry.sh" --probe "$PROBE" --socket "$SOCK" --rounds 1 >"$T/blank.txt" 2>&1; rc=$?
[ "$rc" -eq 1 ] && grep -qx 'get_status_fields missing or malformed: build_identity' "$T/blank.txt" && grep -qx 'poll queries answered 2 of 3' "$T/blank.txt" && ok "telemetry: blank identities are not counted as answered, exit 1" || { bad "telemetry: blank identities (rc $rc)"; cat "$T/blank.txt"; }
kill "$HELPER" 2>/dev/null; wait "$HELPER" 2>/dev/null; HELPER=""

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
