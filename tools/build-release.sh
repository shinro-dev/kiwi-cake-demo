#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# tools/build-release.sh: assemble the release tarballs from a staging
# directory of binaries. Maintainer side; a user never runs this.
#
#   tools/build-release.sh --staging DIR --source-revision HEX40 [--version vX.Y.Z]
#                          [--sign KEYID] [--allow-license-placeholders] [--out DIR]
#                          [--releases-dir DIR]
#
# DIR holds a pi5-aarch64/ subdirectory with the four binaries; one tarball is
# published, and a pi4-aarch64/ directory is ignored with a note. The archive
# is this repository at its tagged commit, laid out as the checkout is, with
# the four binaries added under bin/ beside the scripts; the one tracked path
# left out is releases/<version>/, which cannot exist before the archive
# does. The script:
#   1. refuses unless --version equals the VERSION file (the archive cannot
#      carry a version other than its own tagged commit's);
#   2. refuses while LICENSE still carries a [PLACEHOLDER (unless allowed);
#   3. runs tools/strings-gate.sh over the four binaries and refuses on a hit;
#   4. assembles kiwi-cake-demo-<version>-<slug>/ from every git ls-files
#      path, the four binaries under bin/, and MANIFEST.txt (per-file sha256,
#      source revision, build date), then writes a reproducible tarball
#      (sorted, fixed mtime, numeric owner 0);
# then writes SHA256SUMS over the tarballs, copies it to --releases-dir
# (default releases/<version>/ under this checkout), and, with --sign,
# produces the detached ASCII-armored signatures.
set -uo pipefail
export LC_ALL=C
umask 022
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"

STAGING=""; SRCREV=""; VERSION="$KC_VERSION"; SIGN=""; ALLOW_LIC=0; OUT="$ROOT/dist"; RELEASES_DIR="$ROOT/releases"
while [ $# -gt 0 ]; do
  case "$1" in
    --staging) STAGING="${2:?}"; shift 2 ;;
    --source-revision) SRCREV="${2:?}"; shift 2 ;;
    --version) VERSION="${2:?}"; shift 2 ;;
    --sign) SIGN="${2:?}"; shift 2 ;;
    --allow-license-placeholders) ALLOW_LIC=1; shift ;;
    --out) OUT="${2:?}"; shift 2 ;;
    --releases-dir) RELEASES_DIR="${2:?}"; shift 2 ;;
    -h | --help) sed -n '3,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) kc_fail "unrecognised argument '$1'" 5 ;;
  esac
done
[ "$VERSION" = "$KC_VERSION" ] || kc_fail "--version $VERSION is not the VERSION file's $KC_VERSION; the archive is the repository at its tagged commit and cannot carry another version" 5
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
  case "$s" in
    pi5-aarch64) SLUGS+=("$s") ;;
    pi4-aarch64) kc_say "ignoring $s: one tarball is published and a Raspberry Pi 4 uses pi5-aarch64 (docs/targets.md)" ;;
    *) kc_warn "ignoring unknown staging directory $s" ;;
  esac
done
[ "${#SLUGS[@]}" -eq 1 ] || kc_fail "no pi5-aarch64 directory under $STAGING" 5

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
# Resolved to an absolute path now: the tarball write below runs inside a
# subshell that has already cd'd to a scratch directory, so a relative --out
# would otherwise be interpreted against that scratch directory instead of
# the caller's cwd.
mkdir -p "$OUT" || kc_fail "cannot create $OUT"
OUT="$(cd "$OUT" && pwd)" || kc_fail "cannot resolve $OUT to an absolute path"
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
  # Every tracked file, laid out as in the checkout, so a path that holds in
  # the repository holds inside the archive. Only tracked files reach it: an
  # untracked or ignored file under bin/ (a stray binary, an editor swap
  # file, the private-tokens file under state/) never ships. This version's
  # own checksum list cannot exist before the archive does and is left out
  # even when a later rebuild finds it committed, so a rebuild from the same
  # commit is byte-identical to the first.
  ( cd "$ROOT" && git ls-files -z | grep -zv -- "^releases/$VERSION/" | tar --null -T - -cf - ) | ( cd "$T" && tar -xf - ) ||
    kc_fail "could not copy the tracked files"
  [ -d "$T/THIRD_PARTY_LICENSES" ] && [ -f "$T/keys/shinro-release-signing.pub.asc" ] || kc_fail "the tracked files do not include THIRD_PARTY_LICENSES/ and keys/"
  for b in cake-resident admin-probe demo-plan demo-preflight; do
    [ ! -e "$T/bin/$b" ] || kc_fail "bin/$b is a tracked file; the binary cannot be placed beside the scripts under that name"
    { cp -- "$STAGING/$s/$b" "$T/bin/$b" && chmod 755 "$T/bin/$b"; } || kc_fail "could not place bin/$b"
  done
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
{ mkdir -p "$RELEASES_DIR/$VERSION" && cp -- "$OUT/SHA256SUMS" "$RELEASES_DIR/$VERSION/SHA256SUMS"; } || kc_fail "cannot write $RELEASES_DIR/$VERSION/SHA256SUMS"
kc_say "SHA256SUMS written to $OUT and copied to $RELEASES_DIR/$VERSION/ (commit it)"
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
