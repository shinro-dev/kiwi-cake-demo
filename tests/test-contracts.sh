#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-contracts.sh: the gates of the contract package, which this roster
# did not run before. The schemas under packages/demo-contracts/schema/v1 are
# the source of truth and ts/generated.ts is a build product committed beside
# them, so a schema changed without regenerating the bindings used to reach the
# default branch green. This suite runs the generator's own drift check and the
# two Python suites that validate the fixtures and the runtime against those
# schemas. The bindings are pinned to the node major in .nvmrc: under any other
# major this suite SKIPS that one check by name and says which version it found,
# because a different generator runtime may print the same types differently and
# a silent pass there is exactly the hole this suite exists to close.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE="$ROOT/packages/demo-contracts"
FAILS=0
command -v python3 >/dev/null 2>&1 || { echo "test-contracts: python3 missing"; exit 75; }
[ -d "$PACKAGE/schema/v1" ] || { echo "test-contracts: packages/demo-contracts is not in this checkout"; exit 75; }

# ---- the committed TypeScript bindings still match the schemas --------------
PINNED="$(tr -d '[:space:]' <"$ROOT/.nvmrc" 2>/dev/null)"
if [ -z "$PINNED" ]; then
  echo "FAIL .nvmrc does not declare the node version the bindings are generated under"; FAILS=$((FAILS + 1))
elif [ ! -f "$PACKAGE/ts/generated.ts" ]; then
  echo "FAIL packages/demo-contracts/ts/generated.ts is missing: run pnpm generate and commit it"; FAILS=$((FAILS + 1))
elif ! command -v node >/dev/null 2>&1; then
  echo "SKIP generate --check: no node on the PATH, and the bindings are generated under node $PINNED (.nvmrc)"
else
  HAVE="$(node --version 2>/dev/null)"
  MAJOR="${HAVE#v}"; MAJOR="${MAJOR%%.*}"
  if [ "$MAJOR" != "$PINNED" ]; then
    echo "SKIP generate --check: node $HAVE is on the PATH and the bindings are pinned to node $PINNED (.nvmrc); a different major may print the same types differently"
  elif [ ! -d "$PACKAGE/node_modules/json-schema-to-typescript" ]; then
    echo "SKIP generate --check: the pinned generator is not installed under node $HAVE (run pnpm install at the repository root)"
  else
    OUT="$(cd "$PACKAGE" && node scripts/generate.mjs --check 2>&1)"; RC=$?
    if [ "$RC" -eq 0 ]; then
      echo "ok   generate --check under node $HAVE: $(printf '%s\n' "$OUT" | tail -n 1)"
    else
      echo "FAIL generate --check under node $HAVE (rc=$RC): ts/generated.ts does not match schema/v1; run pnpm generate and commit the result"
      printf '%s\n' "$OUT" | tail -n 20 | sed 's/^/  /'
      FAILS=$((FAILS + 1))
    fi
  fi
fi

# ---- the Python suites that read those same schemas -------------------------
# Discovery is run from the repository root with -t . so that the suites import
# the packages the way the runtime does, and each suite reports the line that
# says how many tests actually ran: a suite that collected nothing is a failure
# that used to look exactly like a suite that passed.
check_suite() {
  local label="$1" path="$2" out rc ran
  if [ ! -d "$ROOT/$path" ]; then
    echo "SKIP $label: $path is not in this checkout"
    return 0
  fi
  out="$(cd "$ROOT" && python3 -m unittest discover -s "$path" -t . 2>&1)"; rc=$?
  ran="$(printf '%s\n' "$out" | grep -E '^Ran [0-9]+ tests? ' | head -n 1)"
  if [ "$rc" -ne 0 ]; then
    echo "FAIL $label (rc=$rc)"; printf '%s\n' "$out" | tail -n 30 | sed 's/^/  /'; FAILS=$((FAILS + 1)); return 0
  fi
  case "$ran" in
    "Ran 0 tests"*|"") echo "FAIL $label collected no test: ${ran:-no count line}"; FAILS=$((FAILS + 1)) ;;
    *) echo "ok   $label: $ran" ;;
  esac
}

check_suite "packages/demo-contracts/tests" "packages/demo-contracts/tests"
check_suite "packages/demo-runtime/tests" "packages/demo-runtime/tests"

[ "$FAILS" -eq 0 ] && echo "test-contracts: PASS" || { echo "test-contracts: $FAILS failure(s)"; exit 1; }
