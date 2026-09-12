#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-templates.sh: the segment-2 templates are refused while a
# placeholder remains outside their comments and accepted once filled as
# documented; the process helpers find a child by parent pid and notice it going.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-templates.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
cp "$ROOT/bin/templates/run-child.sh.in" "$T/run-child.sh"
cp "$ROOT/bin/templates/safe-stop.sh.in" "$T/safe-stop.sh"
kc_template_has_placeholder "$T/run-child.sh" && echo "ok   unfilled run-child.sh is refused" || { echo "FAIL unfilled run-child.sh accepted"; FAILS=$((FAILS + 1)); }
kc_template_has_placeholder "$T/safe-stop.sh" && { echo "FAIL safe-stop.sh has a placeholder to fill"; FAILS=$((FAILS + 1)); } || echo "ok   safe-stop.sh needs nothing filled"
# Fill exactly the six documented placeholders and nothing else.
sed -i -e 's|@@PYTHON@@|/usr/bin/python3|' -e 's|@@HOST_ENTRY@@|/opt/lerobot/host.py|' -e 's|@@ROBOT_ID@@|kiwi|' \
  -e 's|@@ROBOT_PORT@@|/dev/serial/by-id/example|' -e 's|@@CAMERAS_JSON@@|{}|' -e 's|@@CONNECTION_TIME_S@@|86400|' "$T/run-child.sh"
grep -q '@@' "$T/run-child.sh" && echo "     (comment lines still mention @@PLACEHOLDER@@, as documented)"
kc_template_has_placeholder "$T/run-child.sh" && { echo "FAIL filled run-child.sh still refused"; FAILS=$((FAILS + 1)); } || echo "ok   filled run-child.sh is accepted"
grep -q '^exec /usr/bin/python3 /opt/lerobot/host.py' "$T/run-child.sh" && echo "ok   the wrapper execs the host directly" || { echo "FAIL exec line"; FAILS=$((FAILS + 1)); }
grep -vE '^[[:space:]]*#' "$T/run-child.sh" | grep -q '"\$@"' && { echo "FAIL the wrapper forwards the supervisor positionals"; FAILS=$((FAILS + 1)); } || echo "ok   the wrapper ignores the supervisor positionals"
# The filled example: no placeholder, parses, keeps the safety flags, valid cameras JSON.
EX="$ROOT/bin/templates/run-child.sh.example"
kc_template_has_placeholder "$EX" && { echo "FAIL the example still carries a placeholder"; FAILS=$((FAILS + 1)); } || echo "ok   the example carries no placeholder"
bash -n "$EX" && echo "ok   the example parses" || { echo "FAIL the example does not parse"; FAILS=$((FAILS + 1)); }
grep -vE '^[[:space:]]*#' "$EX" | grep -q '"\$@"' && { echo "FAIL the example forwards the supervisor positionals"; FAILS=$((FAILS + 1)); } || echo "ok   the example ignores the supervisor positionals"
for want in '^exec /' 'lekiwi_host_noninteractive.py' '--robot.max_relative_target=15.0' '--robot.disable_torque_on_disconnect=true' '--host.connection_time_s=86400' '/dev/serial/by-id/'; do
  grep -q -- "$want" "$EX" && echo "ok   example has $want" || { echo "FAIL example lacks $want"; FAILS=$((FAILS + 1)); }
done
CAMS="$(grep -o "robot.cameras='[^']*'" "$EX" | sed "s/^robot.cameras='//; s/'\$//")"
printf '%s' "$CAMS" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d and all(v.get("type")=="opencv" and v["index_or_path"].startswith("/dev/v4l/by-id/") and {"width","height","fps","rotation"} <= set(v) for v in d.values())' \
  && echo "ok   example cameras JSON parses and every entry carries type opencv" || { echo "FAIL example cameras JSON"; FAILS=$((FAILS + 1)); }
# The target slug: one tarball for every aarch64 Pi, none elsewhere.
slug_for() { KC_ARCH="$1" KC_MODEL="$2" KC_PAGE_SIZE="$3" KC_GLIBC="$4" kc_target_slug; }
[ "$(slug_for aarch64 'Raspberry Pi 4 Model B Rev 1.5' 4096 2.41)" = "pi5-aarch64" ] && echo "ok   a Pi 4 maps to the pi5-aarch64 tarball" || { echo "FAIL Pi 4 slug"; FAILS=$((FAILS + 1)); }
[ "$(slug_for aarch64 'Raspberry Pi 5 Model B Rev 1.0' 16384 2.41)" = "pi5-aarch64" ] && echo "ok   a Pi 5 maps to the pi5-aarch64 tarball" || { echo "FAIL Pi 5 slug"; FAILS=$((FAILS + 1)); }
[ -z "$(slug_for x86_64 unknown 4096 2.39)" ] && echo "ok   an x86-64 machine has no tarball" || { echo "FAIL x86-64 slug"; FAILS=$((FAILS + 1)); }
# Process helpers.
sleep 30 &
CHILD=$!
case " $(kc_children_of $$) " in *" $CHILD "*) echo "ok   kc_children_of finds the child by parent pid" ;; *) echo "FAIL kc_children_of"; FAILS=$((FAILS + 1)) ;; esac
kill "$CHILD"; wait "$CHILD" 2>/dev/null
kc_wait_gone "$CHILD" 100 && echo "ok   kc_wait_gone sees the child leave" || { echo "FAIL kc_wait_gone"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-templates: PASS" || { echo "test-templates: $FAILS failure(s)"; exit 1; }
