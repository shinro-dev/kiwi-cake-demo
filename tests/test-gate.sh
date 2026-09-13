#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-gate.sh: the strings gate refuses to run without the private-tokens
# file, passes a clean file, fails dirty ones (generic and private patterns),
# and tolerates only an allowlisted entry. Uses its own private-tokens file and
# allowlist under a temporary directory; never touches the committed ones.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v strings >/dev/null 2>&1 || { echo "test-gate: strings (binutils) missing"; exit 75; }
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-gate.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
GATE="$ROOT/tools/strings-gate.sh"
printf '# test tokens\nsomebuilder\nSecretWorkspace\n' >"$T/private.txt"
: >"$T/allow.txt"
export KC_GATE_ALLOWLIST="$T/allow.txt"
printf 'hello\0/cake/src/lib.rs\0/rustc/0123456789abcdef0123456789abcdef01234567/library/std/src/x.rs\0GenuineIntel vendor_id\0' >"$T/clean"
printf 'hello\0/ho%s/someone/work/thing\0' "me" >"$T/dirty-home"
printf 'hello\0after running cra%s/cake-thing/fixtures/build.sh: \0' "tes" >"$T/dirty-relative"
printf 'hello\0the CAR%s_DIR variable\0' "GO_MANIFEST" >"$T/dirty-env"
printf 'hello\0built by somebuilder on SecretWorkspace\0' >"$T/dirty-private"
if KC_GATE_PRIVATE_TOKENS="$T/absent.txt" "$GATE" "$T/clean" >/dev/null 2>&1; then echo "FAIL gate ran without the private-tokens file"; FAILS=$((FAILS + 1)); else echo "ok   refuses without the private-tokens file"; fi
export KC_GATE_PRIVATE_TOKENS="$T/private.txt"
"$GATE" "$T/clean" >/dev/null 2>&1 && echo "ok   clean file passes" || { echo "FAIL clean file"; FAILS=$((FAILS + 1)); }
for d in dirty-home dirty-relative dirty-env dirty-private; do
  if "$GATE" "$T/$d" >/dev/null 2>&1; then echo "FAIL $d passed"; FAILS=$((FAILS + 1)); else echo "ok   $d fails"; fi
done
printf 'the CAR%s_DIR variable\tconstructed for the test\n' "GO_MANIFEST" >"$T/allow.txt"
if "$GATE" "$T/dirty-env" >/dev/null 2>&1; then echo "ok   allowlisted text tolerated"; else echo "FAIL allowlist"; FAILS=$((FAILS + 1)); fi
if "$GATE" "$T/dirty-home" >/dev/null 2>&1; then echo "FAIL allowlist leaked"; FAILS=$((FAILS + 1)); else echo "ok   allowlist is specific"; fi
printf 'e\tshort entries are ignored\n' >"$T/allow.txt"
if "$GATE" "$T/dirty-home" >/dev/null 2>&1; then echo "FAIL a short allowlist entry tolerated a hit"; FAILS=$((FAILS + 1)); else echo "ok   short allowlist entries are ignored"; fi
[ "$FAILS" -eq 0 ] && echo "test-gate: PASS" || { echo "test-gate: $FAILS failure(s)"; exit 1; }
