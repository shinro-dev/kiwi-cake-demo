#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-archive.sh: tools/build-release.sh assembles the tracked tree at
# this checkout plus the four binaries into one release archive; the archive
# runs bin/doctor.sh, tests/smoke-segment1.sh and bin/verify.sh from an
# extraction with no clone and no scripts/ directory. Built with the mock
# binaries in tests/mock-bin standing in for the four real ones; this proves
# the archive layout, not Cake.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for t in git tar gzip sha256sum strings python3; do
  command -v "$t" >/dev/null 2>&1 || { echo "test-archive: $t missing"; exit 75; }
done
# shellcheck source=../bin/lib/common.sh
. "$ROOT/bin/lib/common.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-archive.XXXXXX")"; trap 'rm -rf -- "$T"' EXIT
FAILS=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAILS=$((FAILS + 1)); }

V="$(tr -d '[:space:]' <"$ROOT/VERSION")"
SRCREV="0000000000000000000000000000000000000000"
BUILD="$ROOT/tools/build-release.sh"

# A private-tokens file of our own; the mock binaries carry none of these
# words, so the gate is exercised for real without any private data.
printf '# test-archive private tokens\nkc-test-archive-nonexistent-token\n' >"$T/private.txt"
export KC_GATE_PRIVATE_TOKENS="$T/private.txt"

mkdir -p "$T/staging/pi5-aarch64"
for b in cake-resident admin-probe demo-plan demo-preflight; do
  cp -- "$ROOT/tests/mock-bin/$b" "$T/staging/pi5-aarch64/$b"
done

# --- (1) --version must equal VERSION; refused before anything is created ---
OUT1="$T/dist-bad"; REL1="$T/releases-bad"
"$BUILD" --staging "$T/staging" --source-revision "$SRCREV" --version v9.9.9 \
  --out "$OUT1" --releases-dir "$REL1" >"$T/bad.log" 2>&1
rc1=$?
if [ "$rc1" -eq 5 ]; then ok "a --version that is not VERSION exits 5"; else bad "wrong --version exited $rc1 (want 5)"; sed 's/^/  /' "$T/bad.log"; fi
if [ ! -e "$OUT1" ] && [ ! -e "$REL1" ]; then
  ok "neither --out nor --releases-dir was created for the rejected version"
else
  bad "--out or --releases-dir exists after the version check should have refused first"
fi

# --- (2) the real build, into a scratch --out and --releases-dir ---
OUT="$T/dist"; REL="$T/releases"
REL_STATUS_BEFORE="$(cd "$ROOT" && git status --porcelain -- releases)"
"$BUILD" --staging "$T/staging" --source-revision "$SRCREV" --version "$V" \
  --out "$OUT" --releases-dir "$REL" >"$T/build.log" 2>&1
rc2=$?
REL_STATUS_AFTER="$(cd "$ROOT" && git status --porcelain -- releases)"
TARBALL="$OUT/kiwi-cake-demo-$V-pi5-aarch64.tar.gz"
if [ "$rc2" -eq 0 ] && [ -f "$TARBALL" ] && [ -f "$OUT/SHA256SUMS" ] && [ -f "$REL/$V/SHA256SUMS" ]; then
  ok "the build exits 0 and writes the tarball, SHA256SUMS and the --releases-dir copy"
else
  bad "the build did not produce the expected files (rc $rc2)"; sed 's/^/  /' "$T/build.log"
fi
[ "$REL_STATUS_BEFORE" = "$REL_STATUS_AFTER" ] && ok "the checkout's releases/ is untouched" || bad "the checkout's releases/ changed: $REL_STATUS_AFTER"
grep -q '^strings-gate: PASS' "$T/build.log" && ok "the build log shows strings-gate: PASS" || bad "no strings-gate: PASS in the build log"

# --- (3) the listing: one top directory, the tracked tree, the four binaries, MANIFEST.txt ---
LIST="$(tar -tzf "$TARBALL" 2>/dev/null)"
TOP="$(printf '%s\n' "$LIST" | cut -d/ -f1 | sort -u)"
if [ "$(printf '%s\n' "$TOP" | wc -l)" -eq 1 ] && [ -n "$TOP" ]; then
  ok "the tarball has one top directory ($TOP)"
else
  bad "the tarball does not have exactly one top directory: $TOP"
fi
TOPDIR="$TOP"
printf '%s\n' "$LIST" | grep -qxF "$TOPDIR/releases/$V/SHA256SUMS" &&
  bad "releases/$V/SHA256SUMS is present (it cannot exist before the archive does)" ||
  ok "releases/$V/SHA256SUMS is absent (this version's own checksum list cannot be inside it)"

mapfile -t ARCHIVE_FILES < <(printf '%s\n' "$LIST" | grep -v '/$' | sed "s#^$TOPDIR/##" | sort)
mapfile -t EXPECTED_FILES < <(
  { cd "$ROOT" && git ls-files | grep -v "^releases/$V/"
    printf '%s\n' bin/cake-resident bin/admin-probe bin/demo-plan bin/demo-preflight MANIFEST.txt
  } | sort
)
if [ "$(printf '%s\n' "${ARCHIVE_FILES[@]}")" = "$(printf '%s\n' "${EXPECTED_FILES[@]}")" ]; then
  ok "the archive holds exactly the tracked tree minus releases/$V/, plus the four binaries and MANIFEST.txt"
else
  bad "the archive's file list differs from the tracked tree plus the four binaries"
  diff <(printf '%s\n' "${ARCHIVE_FILES[@]}") <(printf '%s\n' "${EXPECTED_FILES[@]}") | sed 's/^/  /' | head -n 30
fi

mkdir -p "$T/x"
tar -xzf "$TARBALL" -C "$T/x" --strip-components=1 || bad "could not extract the tarball"
for want in keys/shinro-release-signing.pub.asc releases/README.md tests/smoke-segment1.sh tools/strings-gate.sh \
            CONTRIBUTING.md LICENSE-NOTE.md RELEASE_NOTES.md .github/workflows/tests.yml; do
  [ -f "$T/x/$want" ] && ok "present in the archive: $want" || bad "missing from the archive: $want"
done
for b in cake-resident admin-probe demo-plan demo-preflight; do
  [ -x "$T/x/bin/$b" ] && ok "bin/$b is present and executable" || bad "bin/$b is missing or not executable"
done

# Every backticked bin/, docs/, tests/, keys/, tools/, releases/ or .github/
# path in the archive's own README.md, and every named root document, must
# exist inside the extraction -- except the two files the user writes.
mapfile -t BACKTICKED < <(grep -oE '`[A-Za-z0-9_./-]+`' "$T/x/README.md" | tr -d '`' | sort -u)
README_MISSING=()
for p in "${BACKTICKED[@]}"; do
  case "$p" in
    bin/run-child.sh | bin/safe-stop.sh) continue ;;
    bin/* | docs/* | tests/* | keys/* | tools/* | releases/* | .github/* | \
    CONTRIBUTING.md | LICENSE-NOTE.md | SECURITY.md | VERSION | NOTICE | LICENSE)
      [ -e "$T/x/$p" ] || README_MISSING+=("$p") ;;
  esac
done
if [ "${#README_MISSING[@]}" -eq 0 ]; then
  ok "every path README.md names in the archive exists inside it"
else
  bad "README.md names paths missing from the archive: ${README_MISSING[*]}"
fi

MANIFEST="$T/x/MANIFEST.txt"
if [ -f "$MANIFEST" ]; then
  grep -qx "version $V" "$MANIFEST" && ok "MANIFEST.txt names version $V" || bad "MANIFEST.txt does not name version $V"
  grep -qx "source_revision $SRCREV" "$MANIFEST" && ok "MANIFEST.txt names source_revision $SRCREV" || bad "MANIFEST.txt does not name source_revision $SRCREV"
  mapfile -t MANIFEST_FILES < <(sed -nE 's/^  [0-9a-f]{64}  \.\/(.*)$/\1/p' "$MANIFEST" | sort)
  mapfile -t ACTUAL_FILES < <(cd "$T/x" && find . -type f ! -name MANIFEST.txt -printf '%P\n' | sort)
  if [ "$(printf '%s\n' "${MANIFEST_FILES[@]}")" = "$(printf '%s\n' "${ACTUAL_FILES[@]}")" ]; then
    ok "MANIFEST.txt lists exactly the archive's files"
  else
    bad "MANIFEST.txt's file list differs from the archive's actual files"
  fi
else
  bad "MANIFEST.txt is missing from the archive"
fi

# --- (4) the extraction runs the smoke test with no clone, from its own bin/ ---
ARGS=()
[ "$(kc_classify)" = "pi5-tested" ] || ARGS+=(--unsupported-target)
OUT4="$(env -u KC_RELEASE_DIR KC_STATE_DIR="$T/state" "$T/x/tests/smoke-segment1.sh" "${ARGS[@]}" 2>&1)"
rc4=$?
if [ "$rc4" -eq 0 ] && printf '%s\n' "$OUT4" | grep -qx 'smoke-segment1: PASS' && printf '%s\n' "$OUT4" | grep -qF "binaries $T/x/bin"; then
  ok "the extracted archive runs tests/smoke-segment1.sh from its own bin/, with no clone"
else
  bad "the extracted archive's smoke test failed (rc $rc4)"
  printf '%s\n' "$OUT4" | tail -n 20 | sed 's/^/  /'
fi

# --- (5) bin/verify.sh inside the extraction, against a fixture signed by another key ---
if command -v gpg >/dev/null 2>&1; then
  GT="$(mktemp -d "${TMPDIR:-/tmp}/kc-archive-gpg.XXXXXX")"; chmod 700 "$GT"
  if GNUPGHOME="$GT" gpg --batch --quiet --passphrase '' --quick-gen-key 'kc-test-archive <test@invalid>' ed25519 sign never 2>/dev/null; then
    FPR="$(GNUPGHOME="$GT" gpg --batch --with-colons --list-keys 'test@invalid' | awk -F: '/^fpr:/{print $10; exit}')"
    mkdir -p "$T/fixrel" "$T/fixsrc/kiwi-cake-demo-v9.9.9-pi5-aarch64/bin"
    FIXTB="kiwi-cake-demo-v9.9.9-pi5-aarch64.tar.gz"
    echo "not a real binary" >"$T/fixsrc/kiwi-cake-demo-v9.9.9-pi5-aarch64/bin/cake-resident"
    tar -C "$T/fixsrc" -czf "$T/fixrel/$FIXTB" kiwi-cake-demo-v9.9.9-pi5-aarch64
    ( cd "$T/fixrel" && sha256sum "$FIXTB" >SHA256SUMS &&
      GNUPGHOME="$GT" gpg --batch --quiet --passphrase '' --local-user "$FPR" --armor --detach-sign --output SHA256SUMS.asc SHA256SUMS )

    rc5="$("$T/x/bin/verify.sh" --version v9.9.9 "$T/fixrel/$FIXTB" >"$T/verify1.log" 2>&1; echo $?)"
    if [ "$rc5" -eq 3 ]; then
      ok "verify.sh in the extraction reads its own keys/ and refuses a signature by another key (3)"
    else
      bad "verify.sh in the extraction: rc $rc5 (want 3, signed by another key)"; sed 's/^/  /' "$T/verify1.log"
    fi

    cp -- "$T/x/keys/shinro-release-signing.pub.asc" "$T/keys-backup.asc"
    GNUPGHOME="$GT" gpg --batch --armor --export "$FPR" >"$T/x/keys/shinro-release-signing.pub.asc"
    rc6="$(KC_EXPECTED_FINGERPRINT="$FPR" "$T/x/bin/verify.sh" --version v9.9.9 "$T/fixrel/$FIXTB" >"$T/verify2.log" 2>&1; echo $?)"
    if [ "$rc6" -eq 0 ] && grep -q 'no committed checksum list at' "$T/verify2.log"; then
      ok "with the test key and its fingerprint, verify.sh verifies and warns about the missing checksum list"
    else
      bad "verify.sh with the matching test key: rc $rc6 (want 0 with the warning)"; sed 's/^/  /' "$T/verify2.log"
    fi
    cp -- "$T/keys-backup.asc" "$T/x/keys/shinro-release-signing.pub.asc"
  else
    echo "SKIP verify.sh-against-another-key: could not generate an ephemeral gpg key here"
  fi
  rm -rf -- "$GT"
else
  echo "SKIP verify.sh-against-another-key: gpg is not installed"
fi

[ "$FAILS" -eq 0 ] && echo "test-archive: PASS" || { echo "test-archive: $FAILS failure(s)"; exit 1; }
