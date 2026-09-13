#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-ports.sh: the listener helpers in bin/lib/common.sh against a
# fake ss (KC_SS), and the shape of bin/demo-segment2.sh, which cannot run
# here: its port checks call the helpers and its teleop SKIP lines are the
# documented literals.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-ports.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
mkdir -p "$T/bin"
printf '#!/usr/bin/env bash\ncat "$KC_FAKE_SS_TABLE"\n' >"$T/bin/ss"
chmod 755 "$T/bin/ss"
export KC_SS="$T/bin/ss" KC_FAKE_SS_TABLE=""
FAILS=0
ok() { echo "ok   $1"; }
fail() { echo "FAIL $1"; FAILS=$((FAILS + 1)); }
# Tables in the shape ss -tln prints: the header, a decoy on 55555, then the rows under test.
HDR='State  Recv-Q Send-Q Local Address:Port  Peer Address:PortProcess'
row() { printf 'LISTEN 0      5          %s:%s        0.0.0.0:*    %s\n' "$1" "$2" "${3:-}"; }
owner() { printf 'users:(("python",pid=%s,fd=9))' "$1"; }
{ echo "$HDR"; row 0.0.0.0 55555; } >"$T/neither"
{ echo "$HDR"; row 0.0.0.0 55555; row 0.0.0.0 5555; } >"$T/only5555"
{ echo "$HDR"; row 0.0.0.0 55555; row '[::]' 5556; } >"$T/only5556"
{ echo "$HDR"; row 0.0.0.0 55555; row 0.0.0.0 5555; row '[::]' 5556; } >"$T/both"
{ echo "$HDR"; row 0.0.0.0 5555 "$(owner 4242)"; row 0.0.0.0 5556 "$(owner 4242)"; } >"$T/owned"
{ echo "$HDR"; row 0.0.0.0 5555 "$(owner 4242)"; row 0.0.0.0 5556 "$(owner 42420)"; } >"$T/other"
{ echo "$HDR"; row 0.0.0.0 5555 "$(owner 4242)"; row 0.0.0.0 5556; } >"$T/nocolumn"
expect_any() { # expect_any TABLE WANT_RC WANT_PORTS
  local got rc
  KC_FAKE_SS_TABLE="$T/$1"; got="$(kc_any_port_listening 5555 5556)"; rc=$?
  if [ "$rc" -eq "$2" ] && [ "$got" = "$3" ]; then ok "any $1 -> rc $rc [$got]"; else fail "any $1 -> rc $rc [$got] (want $2 [$3])"; fi
}
expect_all() { # expect_all TABLE WANT_RC
  local rc
  KC_FAKE_SS_TABLE="$T/$1"; kc_all_ports_listening 5555 5556; rc=$?
  if [ "$rc" -eq "$2" ]; then ok "all $1 -> rc $rc"; else fail "all $1 -> rc $rc (want $2)"; fi
}
expect_owned() { # expect_owned TABLE WANT_RC
  local rc
  KC_FAKE_SS_TABLE="$T/$1"; kc_port_owned_by 4242 5555 5556; rc=$?
  if [ "$rc" -eq "$2" ]; then ok "owned $1 -> rc $rc"; else fail "owned $1 -> rc $rc (want $2)"; fi
}
expect_any neither 1 ""
expect_any only5555 0 "5555"
expect_any only5556 0 "5556"
expect_any both 0 "5555 5556"
expect_all neither 1
expect_all only5555 1
expect_all only5556 1
expect_all both 0
KC_FAKE_SS_TABLE="$T/only5555"
if kc_port_listening 5555 && ! kc_port_listening 5556; then ok "kc_port_listening sees 5555 and not 5556"; else fail "kc_port_listening on only5555"; fi
expect_owned owned 0
expect_owned other 1
expect_owned nocolumn 2
# The runner's shape: it needs the tested Pi 5 and a terminal, so its text is checked instead.
S="$ROOT/bin/demo-segment2.sh"
has() { if grep -qF -- "$2" "$S"; then ok "$1"; else fail "$1: bin/demo-segment2.sh lacks [$2]"; fi; }
has "startup guard names the held ports" 'kc_fail "something already listens on $HELD;'
has "wait_ports wants every port" 'kc_all_ports_listening "${PORTS[@]}" && return 0'
has "post-stop check names the held ports" 'beat_fail stop "a listener is still on $HELD"'
N="$(grep -cF 'HELD="$(kc_any_port_listening "${PORTS[@]}")"' "$S")"
if [ "$N" -eq 2 ]; then ok "kc_any_port_listening at the guard and after the stop"; else fail "kc_any_port_listening called $N time(s), want 2"; fi
if grep -qE '(^|[^_a-z])ports_listening' "$S"; then fail "a local ports_listening remains"; else ok "no local ports_listening"; fi
G="$(grep -nF 'kc_fail "something already listens on' "$S" | cut -d: -f1 | head -n 1)"
A="$(grep -nF 'ask_ack || kc_fail' "$S" | cut -d: -f1 | head -n 1)"
if [ -n "$G" ] && [ -n "$A" ] && [ "$G" -lt "$A" ]; then ok "the guard runs before the acknowledgment"; else fail "guard/acknowledgment order ($G, $A)"; fi
has "operator-confirmed line" 'teleoperation through the supervised host: operator-confirmed'
has "stop line quoted by the walkthrough" 'echo "stopped: no resident, no host, no socket, no listener"'
has "teleop SKIP beat line" 'echo "KIWI-CAKE beat $BEAT teleop: SKIPPED (operator)"'
has "teleop SKIP last line" 'echo "KIWI-CAKE SEGMENT 2: DONE (teleop SKIPPED by the operator)"'
if grep -qxF '  echo "KIWI-CAKE SEGMENT 2: DONE"' "$S"; then ok "plain last line"; else fail "plain last line"; fi
[ "$FAILS" -eq 0 ] && echo "test-ports: PASS" || { echo "test-ports: $FAILS failure(s)"; exit 1; }
