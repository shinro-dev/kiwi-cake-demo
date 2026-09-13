<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Contributing

## Issues are welcome

Open an issue for:

- a bug in the scripts or in the documentation;
- a reproduction report: the demo ran, or did not, on your board, with the
  facts `bin/doctor.sh` prints;
- a correction to a runbook, where a step did not match what you saw.

A useful issue carries the release version (the `VERSION` file, or the
`version` line of the tarball's `MANIFEST.txt`), the output of
`bin/doctor.sh`, and the transcript under `state/runs/<id>/` with your own
paths and device names removed. Say what you expected and what you saw;
quote the lines verbatim rather than summarising them.

## Pull requests are not accepted

The Cake runtime in this repository is binary-only, under the Business
Source License 1.1. Its scripts and documents are maintained from a
private tree and exported
here, so a pull request against this repository cannot be merged, and any
pull request is closed with a pointer to this page and a request to open an
issue instead. Describe the change you want in the issue; if it is taken,
it appears in a later release with a `RELEASE_NOTES.md` entry.

## Security reports

A vulnerability is not an issue: report it as `SECURITY.md` describes.
