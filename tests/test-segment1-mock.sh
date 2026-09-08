#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-segment1-mock.sh: the smoke test against the mock binaries in
# tests/mock-bin, so the runner's own logic is exercised on any Linux host.
# It proves the runner, not Cake.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "test-segment1-mock: python3 missing (the mock socket helper)"; exit 75; }
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-mock.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
mkdir -p "$T/rel" && ln -s "$ROOT/tests/mock-bin" "$T/rel/bin"
ARGS=()
# The mocks run on any board; on one that is not the tested Pi 5 the runner needs the override.
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
[ "$(kc_classify)" = "pi5-tested" ] || ARGS+=(--unsupported-target)
KC_RELEASE_DIR="$T/rel" KC_STATE_DIR="$T/state" "$ROOT/tests/smoke-segment1.sh" "${ARGS[@]}" >"$T/out.txt" 2>&1
RC=$?
tail -n 3 "$T/out.txt"
[ "$RC" -eq 0 ] && echo "test-segment1-mock: PASS" || { echo "test-segment1-mock: FAIL (rc $RC); transcript follows"; cat "$T/out.txt"; exit 1; }
