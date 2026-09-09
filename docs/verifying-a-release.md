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
bin/fetch-release.sh            # downloads, verifies, extracts
bin/verify.sh release/downloads # verifies an already downloaded set
```

`verify.sh` exits 0 only when every digest matches, every signature is
good, and the signature was made by the fingerprint above. In a repository
checkout it also requires the checksum list to equal the committed copy;
from a tarball's own `scripts/` directory there is no committed copy and it
says so. Its exit codes: 2 for a digest
mismatch, 3 for a missing or bad signature, 4 while the signing key is still
the placeholder, 5 for a usage error.

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
`docs/claims.md` are the statements about that. `tools/strings-gate.sh`
lets you repeat the string scan the release passed on your own download.
