<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Committed checksum lists

One directory per published release, holding the `SHA256SUMS` that
`tools/build-release.sh` wrote when the release was assembled. `bin/verify.sh`
compares a downloaded `SHA256SUMS` against the copy here, so a download is
checked against the repository as well as against the release's signature.

No binary is ever committed here.
