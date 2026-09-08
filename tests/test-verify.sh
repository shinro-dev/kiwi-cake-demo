#!/usr/bin/env bash
# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
# tests/test-verify.sh: bin/verify.sh against a constructed release signed with an
# ephemeral key: the positive case and three negative cases (flipped byte, a
# signature by another key, the placeholder key).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v gpg >/dev/null 2>&1 || { echo "test-verify: gpg missing"; exit 75; }
T="$(mktemp -d "${TMPDIR:-/tmp}/kc-test-verify.XXXXXX")"; chmod 700 "$T"
trap 'rm -rf -- "$T"' EXIT
export GNUPGHOME="$T/gnupg"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
gpg --batch --quiet --passphrase '' --quick-gen-key 'kiwi-cake test key <test@invalid>' ed25519 sign never 2>/dev/null || { echo "test-verify: cannot generate a key"; exit 75; }
gpg --batch --quiet --passphrase '' --quick-gen-key 'kiwi-cake other key <other@invalid>' ed25519 sign never 2>/dev/null
FPR="$(gpg --batch --with-colons --list-keys 'test@invalid' | awk -F: '/^fpr:/{print $10; exit}')"
OTHER="$(gpg --batch --with-colons --list-keys 'other@invalid' | awk -F: '/^fpr:/{print $10; exit}')"
mkdir -p "$T/keys" "$T/rel"
gpg --batch --armor --export "$FPR" >"$T/keys/shinro-release-signing.pub.asc"
TB="kiwi-cake-demo-v9.9.9-pi5-aarch64.tar.gz"
mkdir -p "$T/src/kiwi-cake-demo-v9.9.9-pi5-aarch64/bin"; echo "not a binary" >"$T/src/kiwi-cake-demo-v9.9.9-pi5-aarch64/bin/cake-resident"
tar -C "$T/src" -czf "$T/rel/$TB" kiwi-cake-demo-v9.9.9-pi5-aarch64
( cd "$T/rel" && sha256sum "$TB" >SHA256SUMS && gpg --batch --quiet --passphrase '' --local-user "$FPR" --armor --detach-sign --output SHA256SUMS.asc SHA256SUMS && gpg --batch --quiet --passphrase '' --local-user "$FPR" --armor --detach-sign --output "$TB.asc" "$TB" )
FAILS=0
run() { KC_KEYS_DIR="$T/keys" KC_EXPECTED_FINGERPRINT="$FPR" "$ROOT/bin/verify.sh" --version v9.9.9 "$T/rel/$TB" >"$T/out.txt" 2>&1; echo $?; }
rc="$(run)"; [ "$rc" = 0 ] && echo "ok   positive case verified" || { echo "FAIL positive case rc=$rc"; cat "$T/out.txt"; FAILS=$((FAILS + 1)); }
# flipped byte
cp "$T/rel/$TB" "$T/rel/good.bak"; printf '\x00' | dd of="$T/rel/$TB" bs=1 seek=10 conv=notrunc status=none
rc="$(run)"; [ "$rc" = 2 ] && echo "ok   flipped byte -> digest mismatch (2)" || { echo "FAIL flipped byte rc=$rc"; FAILS=$((FAILS + 1)); }
cp "$T/rel/good.bak" "$T/rel/$TB"
# signed by another key
( cd "$T/rel" && gpg --batch --quiet --yes --passphrase '' --local-user "$OTHER" --armor --detach-sign --output SHA256SUMS.asc SHA256SUMS )
rc="$(run)"; [ "$rc" = 3 ] && echo "ok   other key -> signature refused (3)" || { echo "FAIL other key rc=$rc"; FAILS=$((FAILS + 1)); }
( cd "$T/rel" && gpg --batch --quiet --yes --passphrase '' --local-user "$FPR" --armor --detach-sign --output SHA256SUMS.asc SHA256SUMS )
# a signature by a signing SUBKEY of the release key is accepted (the primary fingerprint is what verify.sh pins)
gpg --batch --quiet --passphrase '' --quick-add-key "$FPR" ed25519 sign never 2>/dev/null
gpg --batch --armor --export "$FPR" >"$T/keys/shinro-release-signing.pub.asc"
SUB="$(gpg --batch --with-colons --list-keys "$FPR" | awk -F: '/^sub:/{print $5; exit}')"
( cd "$T/rel" && gpg --batch --quiet --yes --passphrase '' --local-user "${SUB}!" --armor --detach-sign --output SHA256SUMS.asc SHA256SUMS )
rc="$(run)"; [ "$rc" = 0 ] && echo "ok   subkey signature accepted" || { echo "FAIL subkey rc=$rc"; cat "$T/out.txt"; FAILS=$((FAILS + 1)); }
# a lowercase, spaced fingerprint is normalised
rc="$(KC_KEYS_DIR="$T/keys" KC_EXPECTED_FINGERPRINT="$(printf '%s' "$FPR" | tr 'A-F' 'a-f' | sed 's/..../& /g')" "$ROOT/bin/verify.sh" --version v9.9.9 "$T/rel/$TB" >/dev/null 2>&1; echo $?)"
[ "$rc" = 0 ] && echo "ok   fingerprint normalised" || { echo "FAIL normalisation rc=$rc"; FAILS=$((FAILS + 1)); }
# placeholder key
rc="$(KC_KEYS_DIR="$ROOT/keys" "$ROOT/bin/verify.sh" --version v9.9.9 "$T/rel/$TB" >/dev/null 2>&1; echo $?)"
[ "$rc" = 4 ] && echo "ok   placeholder key -> refused (4)" || { echo "FAIL placeholder rc=$rc"; FAILS=$((FAILS + 1)); }
[ "$FAILS" -eq 0 ] && echo "test-verify: PASS" || { echo "test-verify: $FAILS failure(s)"; exit 1; }
