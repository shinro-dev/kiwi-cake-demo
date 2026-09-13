<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Verifying a release

Every release carries, beside its tarballs:

| Asset | What it is |
| --- | --- |
| `SHA256SUMS` | the SHA-256 digest of every tarball in the release |
| `SHA256SUMS.asc` | a detached, ASCII-armored GPG signature over `SHA256SUMS` |
| `<tarball>.asc` | a detached, ASCII-armored GPG signature over that tarball |

The signing key is `keys/shinro-release-signing.pub.asc` in this repository,
with the fingerprint `5AB8730BB8420601F11965F46E6F9B724BB8EED2` (also stated
in `keys/README.md`). The same `SHA256SUMS` is also committed under
`releases/<version>/` so a download can be checked against the repository as
well as against the release.

## With the tooling

```
bin/fetch-release.sh                              # downloads, verifies, extracts
bin/verify.sh "release/downloads/$(cat VERSION)"   # verifies an already downloaded set
```

`verify.sh` exits 0 only when every digest matches, every signature is
good, and the signature was made by the fingerprint above. Run from a
repository checkout, it also requires the checksum list to equal the copy
committed under `releases/<version>/`. Run from inside an extracted
release archive, there is no committed copy for the archive's own version
(it is computed after the archive exists, so it is the one file that
version's archive cannot contain), and `verify.sh` says so with a warning
instead of failing. Its exit codes: 2 for a digest mismatch, 3 for a
missing or bad signature, 4 while the signing key is still the
placeholder, 5 for a usage error.

## What the archive contains

A release archive is this repository at its tagged commit, laid out
exactly as the checkout is, with the four binaries `cake-resident`,
`admin-probe`, `demo-plan` and `demo-preflight` added under `bin/` beside
the scripts, and a `MANIFEST.txt` at its root. Only tracked files reach
it. Every path named anywhere in this documentation holds inside an
extracted archive, with one exception: `releases/<version>/SHA256SUMS`
for that archive's own version. `bin/doctor.sh`, `tests/smoke-segment1.sh`
and `bin/verify.sh` (with its own `keys/`) all run from an extraction with
no clone; `tests/test-archive.sh` checks this against a build made with
test binaries standing in for the four real ones. What stays
checkout-dependent: the cross-check against `releases/<version>/`,
`tests/run-all.sh` and `tools/build-release.sh` itself, all of which need
git.

## By hand

From a directory holding the tarball, `SHA256SUMS` and `SHA256SUMS.asc`:

```
sha256sum -c --ignore-missing SHA256SUMS
gpg --no-default-keyring --keyring ./kc-keyring.gpg --import /path/to/kiwi-cake-demo/keys/shinro-release-signing.pub.asc
gpg --no-default-keyring --keyring ./kc-keyring.gpg --verify SHA256SUMS.asc SHA256SUMS
gpg --no-default-keyring --keyring ./kc-keyring.gpg --verify kiwi-cake-demo-<version>-<target>.tar.gz.asc kiwi-cake-demo-<version>-<target>.tar.gz
diff SHA256SUMS /path/to/kiwi-cake-demo/releases/<version>/SHA256SUMS
```

Then compare the fingerprint `gpg` prints against `keys/README.md`. A
temporary keyring keeps the release key out of your personal trust store.

## What the checks do and do not prove

A good signature proves the tarball is the one Shinro SAS signed. It does
not prove anything about what the binaries inside do; `LIMITATIONS.md` and
`docs/claims.md` are the statements about that. `tools/strings-gate.sh` is
the scan every released binary passed. It refuses to run without a
private-tokens file (`KC_GATE_PRIVATE_TOKENS`, one extended regular
expression per line), and the maintainer's tokens are not published, so
what you can repeat on your own download is the gate's generic patterns
with a tokens file of your own; the private patterns are the maintainer's
check, not yours.
