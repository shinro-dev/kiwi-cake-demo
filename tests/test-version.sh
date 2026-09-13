#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-version.sh: VERSION is one vX.Y.Z line, common.sh exports it, and
# RELEASE_NOTES.md has a section for it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILS=0
V="$(tr -d '[:space:]' <"$ROOT/VERSION")"
printf '%s' "$V" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$' && echo "ok   VERSION is $V" || { echo "FAIL VERSION is '$V'"; FAILS=$((FAILS + 1)); }
[ "$(wc -l <"$ROOT/VERSION")" -eq 1 ] && echo "ok   VERSION is one line" || { echo "FAIL VERSION is not one line"; FAILS=$((FAILS + 1)); }
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
[ "$KC_VERSION" = "$V" ] && echo "ok   common.sh exports KC_VERSION=$KC_VERSION" || { echo "FAIL KC_VERSION=$KC_VERSION"; FAILS=$((FAILS + 1)); }
grep -qx "## $V" "$ROOT/RELEASE_NOTES.md" && echo "ok   RELEASE_NOTES.md has a $V section" || { echo "FAIL RELEASE_NOTES.md lacks '## $V'"; FAILS=$((FAILS + 1)); }
HARD="$(cd "$ROOT" && grep -rnE '^[[:space:]]*(export[[:space:]]+)?(KC_VERSION=|[A-Z_]*VERSION="v[0-9])' bin tools tests | grep -vE '^bin/lib/common\.sh:[0-9]+:[[:space:]]*KC_VERSION=("\$\(tr |"")')"
if [ -n "$HARD" ]; then echo "FAIL a script hardcodes or reassigns the version:"; printf '%s\n' "$HARD"; FAILS=$((FAILS + 1)); else echo "ok   no script hardcodes a version"; fi
[ "$FAILS" -eq 0 ] && echo "test-version: PASS" || { echo "test-version: $FAILS failure(s)"; exit 1; }
