<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Committed checksum lists

One directory per published release, holding the `SHA256SUMS` that
`tools/build-release.sh` wrote when the release was assembled. `bin/verify.sh`
compares a downloaded `SHA256SUMS` against the copy here, so a download is
checked against the repository as well as against the release's signature.

No binary is ever committed here.

`v0.1.0/` is kept for provenance: that release was withdrawn on 2026-09-12
and its assets were removed from the Releases page (its `LICENSE` carried a
Change Date already in the past). v0.1.1 replaces it with the same binaries.
