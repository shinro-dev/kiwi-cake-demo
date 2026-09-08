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
# Process helpers.
sleep 30 &
CHILD=$!
case " $(kc_children_of $$) " in *" $CHILD "*) echo "ok   kc_children_of finds the child by parent pid" ;; *) echo "FAIL kc_children_of"; FAILS=$((FAILS + 1)) ;; esac
kill "$CHILD"; wait "$CHILD" 2>/dev/null
kc_wait_gone "$CHILD" 100 && echo "ok   kc_wait_gone sees the child leave" || { echo "FAIL kc_wait_gone"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-templates: PASS" || { echo "test-templates: $FAILS failure(s)"; exit 1; }
