<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Reporting a vulnerability

## What counts

Anything in the shipped binaries, the scripts or the documented procedure
that could make a robot move when it should not, run an unsigned or
tampered package, or expose data over the admin socket. If you are unsure
whether something is a vulnerability, report it as one.

## How to report

Use [GitHub's private vulnerability reporting](https://github.com/shinro-dev/kiwi-cake-demo/security/advisories/new)
for this repository.

The report stays private between you and the maintainers until a fix or a
statement is published. The form is active once the repository is public;
a private repository does not offer it.

Never file a vulnerability as a public issue, and do not describe it in a
pull request or a discussion.

## What to include

- the release version (`VERSION` in the checkout, or the `version` line of
  the tarball's `MANIFEST.txt`) and the `source_revision` line of that
  manifest;
- the board facts `bin/doctor.sh` prints;
- the steps to reproduce, and what you saw, quoted verbatim;
- whether a robot moved, and how.

Do not include private signing keys. Select relevant output rather than
attaching the entire state directory. Encrypt anything sensitive to the
release signing key in `keys/` if you wish.

## What to expect

An acknowledgment, then either a fix in a new release with a
`RELEASE_NOTES.md` entry or a statement of why not. No response-time
commitment is published.

## Scope

The Cake runtime is distributed as binaries without its source. The runner
scripts and adapters are published as source in this repository. Reports
about either reach the project maintainers. The
[architecture](docs/architecture.md) explains what the capsule signature
covers and which external host files remain outside it.
