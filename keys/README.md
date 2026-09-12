<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Release signing key

Releases are signed with a GPG key held by Shinro SAS. Two things in this
directory identify it:

| Item | Value |
| --- | --- |
| Public key file | `shinro-release-signing.pub.asc` (ASCII-armored) |
| Fingerprint | `5AB8730BB8420601F11965F46E6F9B724BB8EED2` |

`bin/verify.sh` and `bin/fetch-release.sh` import the key file into a
temporary keyring and check that the signature was made by the fingerprint
above, refusing to verify anything while either is still a placeholder.

To replace them as the maintainer (for a future key rotation):

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

## Rotation policy

The key above signs every v0.1.x release and is kept until one of three
things happens: it is believed compromised, the person holding it changes,
or its algorithm is no longer considered adequate. A rotation ships as a
new release whose `keys/` directory carries the new public key, whose
`RELEASE_NOTES.md` entry names the retired fingerprint and the reason, and
whose `SHA256SUMS` is signed by the new key only. Signatures made with a
retired key stay valid for the releases they signed; `bin/verify.sh` pins
the fingerprint of the checkout it runs from, so verify an old release from
the checkout of that release.
