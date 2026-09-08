#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tools/build-release.sh: assemble the release tarballs from a staging
# directory of binaries. Maintainer side; a user never runs this.
#
#   tools/build-release.sh --staging DIR --source-revision HEX40 [--version vX.Y.Z]
#                          [--sign KEYID] [--allow-license-placeholders] [--out DIR]
#
# DIR holds one subdirectory per target slug (pi5-aarch64, pi4-aarch64), each
# with the four binaries. For every target the script:
#   1. refuses while LICENSE still carries a [PLACEHOLDER (unless allowed);
#   2. runs tools/strings-gate.sh over the four binaries and refuses on a hit;
#   3. assembles kiwi-cake-demo-<version>-<slug>/ with bin/, scripts/ (this
#      repository's bin/ tree), docs/, LICENSE, NOTICE, THIRD_PARTY_LICENSES/
#      and MANIFEST.txt (per-file sha256, source revision, build date);
#   4. writes a reproducible tarball (sorted, fixed mtime, numeric owner 0);
# then writes SHA256SUMS over the tarballs, copies it to releases/<version>/,
# and, with --sign, produces the detached ASCII-armored signatures.
set -uo pipefail
export LC_ALL=C
umask 022
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"

STAGING=""; SRCREV=""; VERSION="$KC_VERSION"; SIGN=""; ALLOW_LIC=0; OUT="$ROOT/dist"
while [ $# -gt 0 ]; do
  case "$1" in
    --staging) STAGING="${2:?}"; shift 2 ;;
    --source-revision) SRCREV="${2:?}"; shift 2 ;;
    --version) VERSION="${2:?}"; shift 2 ;;
    --sign) SIGN="${2:?}"; shift 2 ;;
    --allow-license-placeholders) ALLOW_LIC=1; shift ;;
    --out) OUT="${2:?}"; shift 2 ;;
    -h | --help) sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) kc_fail "unrecognised argument '$1'" 5 ;;
  esac
done
[ -d "$STAGING" ] || kc_fail "--staging DIR is required and must exist" 5
printf '%s' "$SRCREV" | grep -qE '^[0-9a-f]{40}$' || kc_fail "--source-revision must be a 40-character lowercase hex commit id" 5
for t in tar gzip sha256sum; do kc_need_tool "$t" "it is needed to assemble a release"; done
[ -z "$SIGN" ] || kc_need_tool gpg "install gnupg to sign"

# 1. license placeholders
if grep -q '\[PLACEHOLDER' "$ROOT/LICENSE"; then
  if [ "$ALLOW_LIC" -eq 1 ]; then
    kc_warn "LICENSE still carries placeholders; assembling anyway because --allow-license-placeholders was given"
  else
    kc_fail "LICENSE still carries [PLACEHOLDER markers (Additional Use Grant, Change Date, Change License); set them with counsel or pass --allow-license-placeholders for a local dry run" 3
  fi
fi

SLUGS=()
for d in "$STAGING"/*/; do
  [ -d "$d" ] || continue
  s="$(basename "$d")"
  case "$s" in pi5-aarch64 | pi4-aarch64) SLUGS+=("$s") ;; *) kc_warn "ignoring unknown staging directory $s" ;; esac
done
[ "${#SLUGS[@]}" -ge 1 ] || kc_fail "no target directory (pi5-aarch64, pi4-aarch64) under $STAGING" 5

# 2. the gate, over every binary of every target, before anything is assembled
BINS=()
for s in "${SLUGS[@]}"; do
  for b in cake-resident admin-probe demo-plan demo-preflight; do
    [ -f "$STAGING/$s/$b" ] || kc_fail "$STAGING/$s/$b is missing" 5
    BINS+=("$STAGING/$s/$b")
  done
done
kc_say "running the strings gate over ${#BINS[@]} binaries"
"$ROOT/tools/strings-gate.sh" "${BINS[@]}" || kc_fail "the strings gate refused a binary; nothing was assembled" 1

# 3 and 4. assemble
mkdir -p "$OUT" || kc_fail "cannot create $OUT"
# Every file in the tarball carries one mtime: SOURCE_DATE_EPOCH when set,
# else the commit time of this checkout's HEAD; the MANIFEST records the same
# instant as build_date, so two assemblies from one commit are byte-identical.
MTIME="${SOURCE_DATE_EPOCH:-$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || date +%s)}"
BUILD_DATE="$(date -u -d "@$MTIME" +%Y-%m-%d)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kc-build.XXXXXX")"; trap 'rm -rf -- "$WORK"' EXIT
rm -f -- "$OUT"/kiwi-cake-demo-"$VERSION"-*.tar.gz "$OUT"/kiwi-cake-demo-"$VERSION"-*.tar.gz.asc "$OUT/SHA256SUMS" "$OUT/SHA256SUMS.asc"
for s in "${SLUGS[@]}"; do
  NAME="kiwi-cake-demo-$VERSION-$s"
  T="$WORK/$NAME"
  mkdir -p "$T"
  # Only tracked files reach the tarball: an untracked or ignored file under
  # bin/ (a stray binary, an editor swap file) must never ship.
  ( cd "$ROOT" && git ls-files -z bin docs README.md LIMITATIONS.md SAFETY.md LICENSE NOTICE | tar --null -T - -cf - ) | ( cd "$T" && tar -xf - ) ||
    kc_fail "could not copy the tracked scripts and documents"
  mv -- "$T/bin" "$T/scripts" && mkdir -p "$T/bin" || kc_fail "could not lay out $T"
  for b in cake-resident admin-probe demo-plan demo-preflight; do
    cp -- "$STAGING/$s/$b" "$T/bin/$b" && chmod 755 "$T/bin/$b"
  done
  cp -R -- "$ROOT/THIRD_PARTY_LICENSES" "$T/"
  {
    echo "kiwi-cake-demo release manifest"
    echo "version $VERSION"
    echo "target $s"
    echo "source_revision $SRCREV"
    echo "build_date $BUILD_DATE"
    echo "signing_method gpg detached ascii-armored signatures over SHA256SUMS and over each tarball"
    echo "files:"
    ( cd "$T" && find . -type f ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum | sed 's/^/  /' )
  } >"$T/MANIFEST.txt"
  ( cd "$WORK" && tar --sort=name --mtime="@$MTIME" --mode='go-w' --owner=0 --group=0 --numeric-owner -cf - "$NAME" | gzip -n >"$OUT/$NAME.tar.gz" ) ||
    kc_fail "could not write $OUT/$NAME.tar.gz"
  kc_say "assembled $OUT/$NAME.tar.gz"
done
( cd "$OUT" && sha256sum kiwi-cake-demo-"$VERSION"-*.tar.gz >SHA256SUMS ) || kc_fail "cannot write SHA256SUMS"
mkdir -p "$ROOT/releases/$VERSION" && cp -- "$OUT/SHA256SUMS" "$ROOT/releases/$VERSION/SHA256SUMS"
kc_say "SHA256SUMS written to $OUT and copied to releases/$VERSION/ (commit it)"
if [ -n "$SIGN" ]; then
  ( cd "$OUT" && gpg --armor --detach-sign --local-user "$SIGN" --output SHA256SUMS.asc SHA256SUMS ) || kc_fail "signing SHA256SUMS failed"
  for f in "$OUT"/kiwi-cake-demo-"$VERSION"-*.tar.gz; do
    gpg --armor --detach-sign --local-user "$SIGN" --output "$f.asc" "$f" || kc_fail "signing $f failed"
  done
  kc_say "signed with $SIGN"
else
  kc_warn "not signed (no --sign KEYID); the release is incomplete until SHA256SUMS.asc and the tarball signatures exist"
fi
kc_say "done: $(ls "$OUT")"
