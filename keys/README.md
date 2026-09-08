<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Release signing key

Releases are signed with a GPG key held by Shinro SAS. Two things in this
directory identify it:

| Item | Value |
| --- | --- |
| Public key file | `shinro-release-signing.pub.asc` (ASCII-armored) |
| Fingerprint | `PLACEHOLDER_FINGERPRINT_REPLACE_ME` |

Both are placeholders until the maintainer replaces them. `bin/verify.sh` and
`bin/fetch-release.sh` import the key file into a temporary keyring, check
that the signature was made by the fingerprint above, and refuse to verify
anything while either placeholder is still present.

To replace them as the maintainer:

```
gpg --armor --export <KEYID> > keys/shinro-release-signing.pub.asc
gpg --fingerprint <KEYID>
```

and put the 40-character fingerprint (no spaces) in the table above, in
`bin/lib/common.sh` (`KC_RELEASE_KEY_FINGERPRINT`), and in
`docs/verifying-a-release.md`.

Signing method: detached ASCII-armored signatures (`gpg --armor
--detach-sign`) over `SHA256SUMS` and over each release tarball, produced by
`tools/build-release.sh --sign <KEYID>`.
