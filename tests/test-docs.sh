#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-docs.sh: every committed text file carries the copyright header,
# no em-dash, no emoji, no roadmap language, and no string that names the
# private build tree; every script passes shellcheck at warning level.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
FAILS=0
# The house-style rules below apply to text this project WROTE. The exclusions are the files it did not write and may
# not edit: third-party licences, the licence, published release artifacts, the architecture image, signing keys, and
# apps/console-web/design/imported/, which is a design export staged verbatim and pinned by sha256 in
# apps/console-web/design/IMPORT.md. Rewriting a punctuation mark inside that directory would break the digest that
# makes the import checkable, which is the whole point of staging it. It stays in the private-token scan below, which
# asks a different question: that is about leaking a name and applies to every committed byte whoever wrote it.
mapfile -t FILES < <(git ls-files | grep -vE '^(THIRD_PARTY_LICENSES/|LICENSE$|releases/|docs/architecture\.(png|svg)$|keys/.*\.asc$|apps/console-web/design/imported/)')
# grep -P with this class needs PCRE and a UTF-8 locale; probe once on U+1F300.
EMOJI='[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]'
if printf '\xf0\x9f\x8c\x80\n' | grep -qP "$EMOJI" 2>/dev/null; then PCRE=1; else PCRE=0; echo "SKIP emoji scan: grep -P is unusable here (no PCRE support or a non-UTF-8 locale)"; fi
for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  if grep -q $'\xe2\x80\x94' "$f"; then echo "FAIL em-dash in $f"; FAILS=$((FAILS + 1)); fi
  if [ "$PCRE" -eq 1 ] && grep -qP "$EMOJI" "$f"; then echo "FAIL emoji in $f"; FAILS=$((FAILS + 1)); fi
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
LEAK='/home/[a-z]|crates/[a-z]|fixtures/gate|vendor/[a-z]|192\.168\.[0-9]+\.[0-9]+'
PRIVATE="${KC_GATE_PRIVATE_TOKENS:-$ROOT/state/gate-private-tokens.txt}"
if [ -f "$PRIVATE" ]; then
  TOKENS="$(grep -v '^[[:space:]]*#' "$PRIVATE" | grep -v '^[[:space:]]*$' | paste -sd '|')"
  # An empty alternative would match every line, so a file that lists no token is refused.
  if [ -n "$TOKENS" ]; then LEAK="$LEAK|$TOKENS"; else echo "FAIL private-token scan: $PRIVATE lists no token"; FAILS=$((FAILS + 1)); fi
else
  echo "SKIP private-token scan: no tokens file at $PRIVATE (generic patterns only)"
fi
# The two scans below hand grep the file names directly, read NUL-separated so a name with a
# space stays one name, and decide on grep's own exit status: 0 is a hit, 1 is none, anything
# else means a file was not read. The list must not go through xargs: xargs splits a name with
# a space into words grep cannot open and runs grep in batches, and its combined status (123)
# then hides a hit that grep printed. stderr stays visible, because grep reports a match inside
# a binary file there while still exiting 0.
# scan_committed LABEL REGEX FILE...: prints the hits, and counts one failure for a hit, for a
# file it could not read, or for an empty list (which means git listed nothing).
scan_committed() {
  local label="$1" re="$2" rc
  shift 2
  if [ "$#" -eq 0 ]; then echo "FAIL $label: no committed file to scan"; FAILS=$((FAILS + 1)); return; fi
  grep -nHE -- "$re" "$@"
  rc=$?
  case "$rc" in
    0) echo "FAIL $label"; FAILS=$((FAILS + 1)) ;;
    1) ;;
    *) echo "FAIL $label: grep exited $rc, so a committed file was not scanned"; FAILS=$((FAILS + 1)) ;;
  esac
}
LEAK_FILES=()
SCRIPTS=()
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  case "$f" in THIRD_PARTY_LICENSES/* | docs/architecture.png | docs/architecture.svg | tests/test-docs.sh) ;; *) LEAK_FILES+=("$f") ;; esac
  case "$f" in *.sh | *.in) SCRIPTS+=("$f") ;; esac
done < <(git ls-files -z)
scan_committed "private-tree name or source layout in a committed file" "$LEAK" "${LEAK_FILES[@]}"
# No script may hide a token by splitting it into adjacent quoted fragments.
scan_committed "adjacent-quote fragment idiom in a script" "[A-Za-z]\"\"[A-Za-z]|[A-Za-z]''[A-Za-z]" "${SCRIPTS[@]}"
# Shell.
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning -s bash -x -P bin bin/*.sh bin/laptop/*.sh bin/pi/*.sh bin/lib/common.sh tools/*.sh tests/*.sh || { echo "FAIL shellcheck"; FAILS=$((FAILS + 1)); }
else
  echo "SKIP shellcheck: not installed"
fi
[ "$FAILS" -eq 0 ] && echo "test-docs: PASS" || { echo "test-docs: $FAILS failure(s)"; exit 1; }
