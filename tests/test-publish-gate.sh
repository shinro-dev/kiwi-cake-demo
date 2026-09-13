#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-publish-gate.sh: kc_dirty_entries in bin/lib/common.sh, the pure
# function behind tools/publish.sh's step 0 clean-tree check. An empty
# 'git status --porcelain' fed through a heredoc still yields one blank line
# (the newline before the terminator), which the old inline loop's catch-all
# case turned into a dirty entry on a clean tree; this function is read from
# a pipe instead and drops blank lines outright.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
FAILS=0
ok() { echo "ok   $1"; }
fail() { echo "FAIL $1"; FAILS=$((FAILS + 1)); }
expect() { # expect LABEL EXCEPT_PREFIX INPUT WANT
  local label="$1" except="$2" input="$3" want="$4" got rc
  got="$(printf '%s' "$input" | kc_dirty_entries "$except")"; rc=$?
  if [ "$rc" -ne 0 ]; then fail "$label: kc_dirty_entries exited $rc"
  elif [ "$got" = "$want" ]; then ok "$label"
  else fail "$label: got [$got], want [$want]"; fi
}
expect "empty input prints nothing" "?? releases/v0.1.3/" "" ""
expect "only the tolerated entry prints nothing" "?? releases/v0.1.3/" "?? releases/v0.1.3/SHA256SUMS
" ""
expect "a tolerated entry plus a real change prints exactly the change" "?? releases/v0.1.3/" "?? releases/v0.1.3/SHA256SUMS
 M README.md
" " M README.md"
[ "$FAILS" -eq 0 ] && echo "test-publish-gate: PASS" || { echo "test-publish-gate: $FAILS failure(s)"; exit 1; }
