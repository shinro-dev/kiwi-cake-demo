#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tools/strings-gate.sh: the string scan every released binary must pass.
# Reads each binary as raw bytes (no printable-run threshold) and as
# `strings` output, and fails on any of:
#   an absolute home path (/home/), any path under crates/, fixtures/ or
#   vendor/, the cargo manifest variable, the run-time repository-root
#   variable, and every token listed in the private-tokens file.
#
# The private-tokens file (one extended regex per line, '#' comments) names
# the things that must never leave the build host: the builder's user name,
# the workspace directory, the private repository name. It is deliberately
# NOT committed; the gate refuses to run without it so that a scan can never
# silently cover less than it should. Path: $KC_GATE_PRIVATE_TOKENS or
# state/gate-private-tokens.txt.
#
# Lines listed in the allowlist ($KC_GATE_ALLOWLIST or
# tools/gate-allowlist.txt) are tolerated when the allowlisted text (at least
# 16 characters) is contained in the hit; each such line is a disclosed
# residual and must also appear in LIMITATIONS.md. A constructed dirty file is
# scanned first so that a scan that finds nothing cannot pass by accident.
#
# Usage: tools/strings-gate.sh BINARY... ; exit 0 clean, 1 hits, 2 usage.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALLOW="${KC_GATE_ALLOWLIST:-$ROOT/tools/gate-allowlist.txt}"
PRIVATE="${KC_GATE_PRIVATE_TOKENS:-$ROOT/state/gate-private-tokens.txt}"
[ $# -ge 1 ] || { echo "usage: tools/strings-gate.sh BINARY..." >&2; exit 2; }
command -v strings >/dev/null 2>&1 || { echo "strings-gate: strings (binutils) is not on PATH" >&2; exit 2; }
[ -f "$PRIVATE" ] || { echo "strings-gate: the private-tokens file $PRIVATE is missing; the gate refuses to scan with less than its full pattern set (see the header of this script)" >&2; exit 2; }

GENERIC_RE='/home/|/crates/|/vendor/|CARGO_MANIFEST|CAKE_REPO_ROOT'
PRIVATE_RE="$(grep -v '^[[:space:]]*#' "$PRIVATE" | grep -v '^[[:space:]]*$' | paste -sd '|')"
[ -n "$PRIVATE_RE" ] || { echo "strings-gate: $PRIVATE lists no token" >&2; exit 2; }
STRINGS_RE="$GENERIC_RE|$PRIVATE_RE"
RAW_RE='(^|[^A-Za-z0-9_])(crates/|fixtures/|vendor/)[A-Za-z0-9_.-]+/'

scan() {
  # scan FILE: each hit as "<pattern-class>\t<matched text with bounded context>", deduplicated.
  {
    strings -- "$1" | grep -oiE -- ".{0,48}($STRINGS_RE).{0,96}" | sed 's/^/strings\t/'
    grep -a -o -E -- "$RAW_RE" "$1" | sed 's/^/raw\t/'
  } 2>/dev/null | sort -u
}
allowed() {
  # allowed HIT: true when an allowlist entry of at least 16 characters is contained in the hit.
  local entry
  while IFS=$'\t' read -r entry _; do
    [ -n "$entry" ] || continue
    case "$entry" in '#'*) continue ;; esac
    [ "${#entry}" -ge 16 ] || continue
    case "$1" in *"$entry"*) return 0 ;; esac
  done <"$ALLOW"
  return 1
}

# --- the nonzero control -------------------------------------------------------------
CTRL="$(mktemp)"; trap 'rm -f -- "$CTRL"' EXIT
c="crates"; e="CARGO_MANIFEST"
printf 'x\0%s/somebody/build/%s/cake-thing/src/lib.rs\0%s_DIR\0' "/home" "$c" "$e" >"$CTRL"
[ "$(scan "$CTRL" | wc -l)" -ge 2 ] || { echo "strings-gate: the control file produced no hit, so the scan proves nothing" >&2; exit 2; }

TOTAL=0
for bin in "$@"; do
  [ -f "$bin" ] || { echo "strings-gate: $bin is not a file" >&2; exit 2; }
  hits=0; tolerated=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    if allowed "$line"; then
      tolerated=$((tolerated + 1)); echo "  tolerated (allowlisted): ${line:0:200}"
    else
      hits=$((hits + 1)); echo "  HIT: ${line:0:240}"
    fi
  done < <(scan "$bin")
  if [ "$hits" -eq 0 ]; then
    echo "CLEAN  $bin ($tolerated tolerated)"
  else
    echo "DIRTY  $bin ($hits hit(s), $tolerated tolerated)"
  fi
  TOTAL=$((TOTAL + hits))
done
[ "$TOTAL" -eq 0 ] && { echo "strings-gate: PASS ($# file(s))"; exit 0; }
echo "strings-gate: FAIL ($TOTAL hit(s) across $# file(s))"; exit 1
