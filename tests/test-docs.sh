#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-docs.sh: every committed text file carries the copyright header,
# no em-dash, no emoji, no roadmap language, and no string that names the
# private build tree; every script passes shellcheck at warning level.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
FAILS=0
mapfile -t FILES < <(git ls-files | grep -vE '^(THIRD_PARTY_LICENSES/|LICENSE$|releases/|docs/architecture\.(png|svg)$|keys/.*\.asc$)')
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  if grep -q $'\xe2\x80\x94' "$f"; then echo "FAIL em-dash in $f"; FAILS=$((FAILS + 1)); fi
  if grep -qP '[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]' "$f" 2>/dev/null; then echo "FAIL emoji in $f"; FAILS=$((FAILS + 1)); fi
  case "$f" in
    *.md | *.sh | *.in | tools/* | bin/* | tests/* | .github/*)
      case "$f" in tests/mock-bin/*|tests/fixtures/*|tools/gate-allowlist.txt) ;; *)
        head -n 3 "$f" | grep -q 'Copyright 2026 Shinro SAS' || { echo "FAIL missing copyright header in $f"; FAILS=$((FAILS + 1)); } ;;
      esac ;;
  esac
done
# Roadmap language in the user-facing documents.
for f in README.md LIMITATIONS.md SAFETY.md docs/*.md; do
  if grep -niE 'roadmap|coming soon|next release|will be added|is planned|future release' "$f" | grep -v 'no roadmap\|There is no roadmap\|nothing here should be read'; then
    echo "FAIL roadmap language in $f"; FAILS=$((FAILS + 1))
  fi
done
# Nothing committed may name a home directory, a source tree layout, or (from the
# untracked private-tokens file, when present) the private tree, its owner or its
# machine. The private tokens live outside git on purpose: this test must not carry them.
LEAK='/home/[a-z]|crates/[a-z]|fixtures/gate|vendor/[a-z]'
PRIVATE="${KC_GATE_PRIVATE_TOKENS:-$ROOT/state/gate-private-tokens.txt}"
if [ -f "$PRIVATE" ]; then
  LEAK="$LEAK|$(grep -v '^[[:space:]]*#' "$PRIVATE" | grep -v '^[[:space:]]*$' | paste -sd '|')"
else
  echo "note: no private-tokens file at $PRIVATE; scanning generic patterns only"
fi
if git ls-files | grep -vE '^(THIRD_PARTY_LICENSES/|docs/architecture\.(png|svg)$)' | xargs grep -nE -- "$LEAK" 2>/dev/null | grep -v '^tests/test-docs.sh:'; then
  echo "FAIL private-tree name or source layout in a committed file"; FAILS=$((FAILS + 1))
fi
# No script may hide a token by splitting it into adjacent quoted fragments.
if git ls-files | grep -E '\.(sh|in)$' | xargs grep -nE "[A-Za-z]\"\"[A-Za-z]|[A-Za-z]''[A-Za-z]" 2>/dev/null; then
  echo "FAIL adjacent-quote fragment idiom in a script"; FAILS=$((FAILS + 1))
fi
# Shell.
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning -s bash -x -P bin bin/*.sh bin/lib/common.sh tools/*.sh tests/*.sh || { echo "FAIL shellcheck"; FAILS=$((FAILS + 1)); }
else
  echo "note: shellcheck not installed, skipped"
fi
[ "$FAILS" -eq 0 ] && echo "test-docs: PASS" || { echo "test-docs: $FAILS failure(s)"; exit 1; }
