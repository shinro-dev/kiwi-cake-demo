<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Public-repository security and privacy

## Principle

The upstream repository is public, but development remains local-only until
publication approval. Prevent sensitive material from entering branch history,
not merely the final tree.

## Development controls

- ignored raw-data directories;
- pre-commit scanner;
- pre-push hard block;
- synthetic fixtures only;
- no dev signing material in tracked files;
- branch baseline recorded locally.

## Publication controls

Before first push:
- E2E success confirmed;
- independent review completed;
- full branch-history scan passes;
- final tree scan passes;
- license review passes;
- claims/evidence review passes;
- final release artifacts intentionally selected;
- human explicitly authorizes push.

## Never commit during development

- private keys/credentials/tokens;
- raw camera media;
- datasets/episodes/checkpoints;
- private Cake source;
- local Cake dev signatures;
- environment dumps;
- machine/user/network identifiers;
- PII from camera scenes;
- local runtime state.

## Release signing

Private signing key stays outside this repository permanently.

A final approved Cake binary/checksum/public signature/public verification key
may be intentionally published only through the release process after review.

## Camera privacy

All raw robot imagery is sensitive by default.

Any eventual public screenshot/video requires explicit frame-by-frame review,
metadata stripping/cropping and approval.

## History matters

Deleting a sensitive file in a later commit does not make it safe to push.

`tools/publication_audit.py` scans objects introduced anywhere in the branch
history since `.local-dev/base-commit`.

If a real credential ever enters commit history, rotate/revoke it even if the
branch was never pushed; then rewrite/discard the local branch before
publication.
