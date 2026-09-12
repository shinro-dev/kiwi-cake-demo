#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/fetch-release.sh: detect the board, download the matching release
# tarball with its checksum list and signatures, verify them with
# bin/verify.sh, and extract the tarball under release/<target>/.
# Nothing is extracted before verification passes.
#
# Options:
#   --target SLUG    pi5-aarch64, the only published tarball (default: detected)
#   --version vX.Y.Z (default: this checkout's release)
#   --from DIR       use assets already downloaded into DIR (offline)
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

VERSION="$KC_VERSION"
SLUG=""
FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target) SLUG="${2:?--target needs a slug}"; shift 2 ;;
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --from) FROM="${2:?--from needs a directory}"; shift 2 ;;
    -h | --help) echo "usage: bin/fetch-release.sh [--target pi5-aarch64] [--version vX.Y.Z] [--from DIR]"; exit 0 ;;
    *) kc_fail "unrecognised argument '$1'" 5 ;;
  esac
done
kc_need_tool tar "install tar"
kc_need_tool sha256sum "it ships with coreutils"
kc_need_tool gpg "install gnupg"

if [ -z "$SLUG" ]; then
  kc_facts
  SLUG="$(kc_target_slug)"
  case "$(kc_classify)" in
    x86_64) kc_fail "this is an x86-64 machine and no binaries are published for it in this release (docs/targets.md)" 3 ;;
    other) kc_fail "this machine ($KC_ARCH, $KC_MODEL) is not one the release describes; pass --target explicitly if you know what you are doing" 3 ;;
  esac
  kc_say "detected $KC_MODEL ($KC_ARCH), page size $KC_PAGE_SIZE, glibc $KC_GLIBC: target $SLUG"
fi
case "$SLUG" in
  pi5-aarch64) : ;;
  pi4-aarch64) kc_fail "one tarball is published, pi5-aarch64; a Raspberry Pi 4 uses it (docs/targets.md)" 5 ;;
  *) kc_fail "unknown target slug '$SLUG'" 5 ;;
esac

TARBALL="kiwi-cake-demo-$VERSION-$SLUG.tar.gz"
DL="$KC_ROOT/release/downloads/$VERSION"
mkdir -p "$DL" || kc_fail "cannot create $DL"

if [ -n "$FROM" ]; then
  FROM="$(cd "$FROM" && pwd)" || kc_fail "--from $FROM is not a directory" 5
  for a in "$TARBALL" "$TARBALL.asc" SHA256SUMS SHA256SUMS.asc; do
    [ -f "$FROM/$a" ] || kc_fail "$FROM/$a is missing" 5
    [ "$FROM" = "$DL" ] || cp -- "$FROM/$a" "$DL/" || kc_fail "cannot copy $a"
  done
else
  BASE="https://github.com/$KC_REPO/releases/download/$VERSION"
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    kc_say "downloading with gh from $KC_REPO $VERSION"
    for a in "$TARBALL" "$TARBALL.asc" SHA256SUMS SHA256SUMS.asc; do
      rm -f -- "$DL/$a"
      gh release download "$VERSION" --repo "$KC_REPO" --pattern "$a" --dir "$DL" ||
        kc_fail "gh could not download $a from $KC_REPO $VERSION"
    done
  elif command -v curl >/dev/null 2>&1; then
    kc_say "downloading with curl from $BASE"
    for a in "$TARBALL" "$TARBALL.asc" SHA256SUMS SHA256SUMS.asc; do
      curl -fsSL -o "$DL/$a" "$BASE/$a" || kc_fail "curl could not download $BASE/$a"
    done
  else
    kc_fail "neither gh nor curl is on PATH; download the four assets by hand and pass --from DIR" 5
  fi
fi

kc_say "verifying $DL"
"$KC_SCRIPTS_DIR/verify.sh" --version "$VERSION" "$DL/$TARBALL" || exit $?

TOP="$(tar -tzf "$DL/$TARBALL" | cut -d/ -f1 | sort -u)"
[ "$TOP" = "kiwi-cake-demo-$VERSION-$SLUG" ] || kc_fail "the tarball's top directory is '$TOP', expected kiwi-cake-demo-$VERSION-$SLUG; refusing to extract" 2
DEST="$KC_ROOT/release/$SLUG"
rm -rf -- "$DEST"
mkdir -p "$DEST" || kc_fail "cannot create $DEST"
tar -xzf "$DL/$TARBALL" -C "$DEST" --strip-components=1 || kc_fail "extraction failed"
kc_check_binaries "$DEST/bin"
kc_say "extracted to $DEST"
kc_say "next: bin/doctor.sh, then bin/demo-segment1.sh"
