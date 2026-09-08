#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
#
# bin/verify.sh: verify a downloaded release. Given a directory holding one or
# more release tarballs together with SHA256SUMS and SHA256SUMS.asc (or the
# path of one tarball whose siblings are those files), it checks:
#   1. every tarball's SHA-256 digest against SHA256SUMS;
#   2. the detached signature over SHA256SUMS, made by the release key;
#   3. the detached signature over each tarball, when present;
#   4. SHA256SUMS against the copy committed under releases/<version>/,
#      when this script runs from a repository checkout that has it.
# The release key is imported into a temporary keyring, never into yours.
#
# Exit codes: 0 verified; 2 a digest did not match; 3 a signature is missing
# or bad, or made by another key; 4 the release key in keys/ is still the
# placeholder; 5 usage or a missing tool.
set -uo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

VERSION="$KC_VERSION"
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    -h | --help) echo "usage: bin/verify.sh [--version vX.Y.Z] DIRECTORY_OR_TARBALL"; exit 0 ;;
    *) [ -z "$TARGET" ] || kc_fail "only one directory or tarball may be given" 5; TARGET="$1"; shift ;;
  esac
done
[ -n "$TARGET" ] || kc_fail "usage: bin/verify.sh [--version vX.Y.Z] DIRECTORY_OR_TARBALL" 5
kc_need_tool sha256sum "it ships with coreutils"
kc_need_tool gpg "install gnupg"

if [ -f "$TARGET" ]; then
  DIR="$(cd "$(dirname "$TARGET")" && pwd)"
  TARBALLS=("$(basename "$TARGET")")
elif [ -d "$TARGET" ]; then
  DIR="$(cd "$TARGET" && pwd)"
  TARBALLS=()
  for f in "$DIR"/*.tar.gz; do
    [ -f "$f" ] && TARBALLS+=("$(basename "$f")")
  done
  [ "${#TARBALLS[@]}" -ge 1 ] || kc_fail "$DIR holds no .tar.gz" 5
else
  kc_fail "$TARGET is neither a file nor a directory" 5
fi
[ -f "$DIR/SHA256SUMS" ] || kc_fail "$DIR/SHA256SUMS is missing" 2
[ -f "$DIR/SHA256SUMS.asc" ] || kc_fail "$DIR/SHA256SUMS.asc is missing" 3

KEYFILE="$KC_KEYS_DIR/shinro-release-signing.pub.asc"
EXPECTED_FPR="${KC_EXPECTED_FINGERPRINT:-$KC_RELEASE_KEY_FINGERPRINT}"
[ -f "$KEYFILE" ] || kc_fail "release key $KEYFILE is missing" 4
if grep -q 'PLACEHOLDER' "$KEYFILE" || [ "$EXPECTED_FPR" = "PLACEHOLDER_FINGERPRINT_REPLACE_ME" ]; then
  kc_fail "the release signing key in keys/ is still a placeholder; nothing can be verified against it (keys/README.md)" 4
fi
EXPECTED_FPR="$(printf '%s' "$EXPECTED_FPR" | tr -d ' ' | tr 'a-f' 'A-F')"
printf '%s' "$EXPECTED_FPR" | grep -qE '^[0-9A-F]{40}$' ||
  kc_fail "the expected fingerprint is not 40 hexadecimal characters; fix KC_RELEASE_KEY_FINGERPRINT (keys/README.md)" 4

# --- 1. digests ------------------------------------------------------------------
for t in "${TARBALLS[@]}"; do
  expected="$(awk -v f="$t" '$2 == f || $2 == "*" f { print $1; exit }' "$DIR/SHA256SUMS")"
  [ -n "$expected" ] || kc_fail "SHA256SUMS carries no line for $t" 2
  actual="$(sha256sum "$DIR/$t" | cut -d' ' -f1)"
  if [ "$actual" = "$expected" ]; then
    kc_say "digest ok: $t"
  else
    kc_fail "digest MISMATCH for $t (expected $expected, got $actual)" 2
  fi
done

# --- 2 and 3. signatures -----------------------------------------------------------
GNUPGTMP="$(mktemp -d "${TMPDIR:-/tmp}/kiwi-cake-gpg.XXXXXX")" || kc_fail "cannot create a temporary keyring" 5
chmod 700 "$GNUPGTMP"
trap 'rm -rf -- "$GNUPGTMP"' EXIT
KEYRING="$GNUPGTMP/release.kbx"
gpg --homedir "$GNUPGTMP" --batch --quiet --no-default-keyring --keyring "$KEYRING" --import "$KEYFILE" 2>/dev/null ||
  kc_fail "the release key could not be imported" 4

kc_verify_sig() {
  # kc_verify_sig SIGFILE DATAFILE: good signature by the expected fingerprint.
  local status
  status="$(gpg --homedir "$GNUPGTMP" --batch --no-default-keyring --keyring "$KEYRING" \
    --status-fd 1 --verify "$1" "$2" 2>/dev/null)"
  printf '%s\n' "$status" | grep -q '^\[GNUPG:\] GOODSIG ' || return 1
  printf '%s\n' "$status" | grep -qE "^\[GNUPG:\] VALIDSIG [0-9A-Fa-f]{40} .* $EXPECTED_FPR\$|^\[GNUPG:\] VALIDSIG $EXPECTED_FPR " || return 2
  return 0
}

kc_verify_sig "$DIR/SHA256SUMS.asc" "$DIR/SHA256SUMS"
case $? in
  0) kc_say "signature ok: SHA256SUMS" ;;
  1) kc_fail "the signature over SHA256SUMS is bad or missing" 3 ;;
  2) kc_fail "SHA256SUMS is signed, but not by the release key fingerprint in keys/README.md" 3 ;;
esac
for t in "${TARBALLS[@]}"; do
  if [ -f "$DIR/$t.asc" ]; then
    kc_verify_sig "$DIR/$t.asc" "$DIR/$t"
    case $? in
      0) kc_say "signature ok: $t" ;;
      1) kc_fail "the signature over $t is bad" 3 ;;
      2) kc_fail "$t is signed, but not by the release key" 3 ;;
    esac
  else
    kc_warn "$t.asc is absent; the tarball is covered by the signed SHA256SUMS only"
  fi
done

# --- 4. the committed checksum list ------------------------------------------------
COMMITTED="$KC_ROOT/releases/$VERSION/SHA256SUMS"
if [ -f "$COMMITTED" ]; then
  if cmp -s -- "$DIR/SHA256SUMS" "$COMMITTED"; then
    kc_say "SHA256SUMS equals the copy committed under releases/$VERSION"
  else
    kc_fail "SHA256SUMS differs from the copy committed under releases/$VERSION" 2
  fi
else
  kc_warn "no committed checksum list at releases/$VERSION/SHA256SUMS to cross-check (not a repository checkout, or a different version)"
fi
kc_say "VERIFIED: ${#TARBALLS[@]} tarball(s) in $DIR"
exit 0
