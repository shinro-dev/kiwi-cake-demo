#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/doctor.sh: read the facts of this board, classify it against the tested
# record, probe each shipped binary the way the preflight's seventh check
# would, and explain in plain language what demo-preflight will do here.
#
# With --capsule PATH --trusted-key HEX it also runs demo-preflight itself
# against a staged package (the demo runners do this for you) and explains
# any refusal.
#
# Exit codes: 0 tested board and every binary runs; 3 the board is not the
# tested one (informational, segment 1 may still run with --unsupported-target);
# 4 a shipped binary did not start; 5 no release binaries found or usage.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

CAPSULE=""
TRUSTED=""
SCRATCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --capsule) CAPSULE="${2:?--capsule needs a path}"; shift 2 ;;
    --trusted-key) TRUSTED="${2:?--trusted-key needs a hex key}"; shift 2 ;;
    --store-scratch) SCRATCH="${2:?--store-scratch needs a directory}"; shift 2 ;;
    -h | --help)
      echo "usage: bin/doctor.sh [--capsule PATH --trusted-key HEX [--store-scratch DIR]]"
      exit 0 ;;
    *) kc_fail "unrecognised argument '$1'" 5 ;;
  esac
done

kc_facts
CLASS="$(kc_classify)"
echo "kiwi-cake doctor"
echo "  architecture     $KC_ARCH"
echo "  board model      $KC_MODEL"
echo "  operating system ${KC_OS:-unknown}"
echo "  kernel           $KC_KERNEL"
echo "  page size        $KC_PAGE_SIZE"
echo "  glibc            $KC_GLIBC"
echo "  memfd_noexec     $KC_MEMFD"
echo "  classification   $CLASS"
echo
echo "The tested board record: Raspberry Pi 5, 16384-byte pages, glibc 2.41."
case "$CLASS" in
  pi5-tested)
    echo "This board matches the tested record on every fact demo-preflight compares." ;;
  pi5-untested-os)
    echo "This is a Raspberry Pi 5, but not the tested configuration:"
    [ "$KC_PAGE_SIZE" = "16384" ] || echo "  page size $KC_PAGE_SIZE: demo-preflight will refuse at check page-size (boot the default 16 KiB kernel to pass)."
    if [ "$KC_GLIBC" = "unknown" ] || ! kc_version_ge "$KC_GLIBC" "2.41"; then
      echo "  glibc $KC_GLIBC: demo-preflight will refuse at check glibc (the record is 2.41; Raspberry Pi OS trixie carries it)."
    fi
    echo "Segment 1 can run with --unsupported-target; segment 2 cannot run here. See docs/targets.md." ;;
  pi4)
    echo "This is a Raspberry Pi 4: demo-preflight will refuse at check page-size by construction."
    echo "Segment 1 can run with --unsupported-target (untested); segment 2 cannot run here. See docs/targets.md." ;;
  x86_64)
    echo "This is an x86-64 machine: no binaries are published for it in this release, and the"
    echo "compiled-in target profile is the aarch64 LeKiwi profile. Nothing beyond usage output can run here." ;;
  *)
    echo "This machine is not one the release describes. See docs/targets.md." ;;
esac
if [ "$KC_MEMFD" = "2" ]; then
  echo "vm.memfd_noexec_scope is 2: demo-preflight will refuse at check memfd-noexec on any board."
fi
echo

BIN_DIR="$(kc_binaries_dir 2>/dev/null)" || { echo "No release binaries found; run bin/fetch-release.sh first."; exit 5; }
kc_check_binaries "$BIN_DIR"
echo "Shipped binaries under $BIN_DIR (one no-op run each, the shell equivalent of the preflight's shipped-binaries check):"
BAD=0
for b in cake-resident admin-probe demo-plan demo-preflight; do
  if kc_probe_binary "$BIN_DIR/$b"; then
    echo "  $b: starts and resolves under this board's dynamic loader"
  else
    echo "  $b: DID NOT START (dynamic loader or signal failure)"
    BAD=$((BAD + 1))
  fi
done
echo
echo "demo-preflight publishes these checks, in order:"
"$BIN_DIR/demo-preflight" --list-checks 2>/dev/null | sed 's/^/  /'
echo "In this release the observer check always refuses (LIMITATIONS.md); five green checks then that refusal is the accepted shape."
echo

if [ -n "$CAPSULE" ]; then
  [ -n "$TRUSTED" ] || kc_fail "--capsule needs --trusted-key" 5
  [ -n "$SCRATCH" ] || SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/kiwi-cake-preflight.XXXXXX")"
  OUT="$(mktemp)"; ERR="$(mktemp)"
  "$BIN_DIR/demo-preflight" --capsule "$CAPSULE" --trusted-key "$TRUSTED" --store-scratch "$SCRATCH" \
    --binary "$BIN_DIR/cake-resident" --binary "$BIN_DIR/admin-probe" \
    --binary "$BIN_DIR/demo-plan" --binary "$BIN_DIR/demo-preflight" >"$OUT" 2>"$ERR"
  RC=$?
  VERDICT="$(kc_classify_preflight "$OUT" "$ERR" "$RC")"
  echo "demo-preflight output:"
  sed 's/^/  /' "$OUT"
  sed 's/^/  /' "$ERR"
  echo
  case "$VERDICT" in
    pass) echo "Preflight verdict: PASS (every check)." ;;
    five-green-observer-refused) echo "Preflight verdict: five checks green, observer refused as documented. Accepted." ;;
    refused:*)
      check="${VERDICT#refused:}"; check="${check%%:*}"
      msg="${VERDICT#refused:*:}"
      echo "Preflight verdict: REFUSED."
      kc_explain_refusal "$check" "$msg" ;;
    *) echo "Preflight verdict: could not be parsed (${VERDICT#unparsed:}); the raw output is above."; kc_explain_unparsed "$VERDICT" ;;
  esac
  rm -f -- "$OUT" "$ERR"
fi

[ "$BAD" -eq 0 ] || exit 4
[ "$CLASS" = "pi5-tested" ] || exit 3
exit 0
