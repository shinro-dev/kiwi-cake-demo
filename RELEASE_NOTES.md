<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Release notes

## v0.1.0

The first public release of the Cake demo for LeKiwi owners. Released as-is,
for evaluation, under the Business Source License 1.1.

What is in it:

- Four precompiled binaries per target: `cake-resident`, `admin-probe`,
  `demo-plan`, `demo-preflight`. Targets: `pi5-aarch64` (tested and primary)
  and `pi4-aarch64` (provided as-is; the same bytes as `pi5-aarch64`).
- The scripts, documentation and license files of this repository at the
  tagged commit, inside every tarball.
- `SHA256SUMS`, its detached signature, and a detached signature per tarball.

What is not in it: any source code; an x86-64 build; live module
replacement; actuator-safe recovery. `LIMITATIONS.md` has the full list.

Checksums: `releases/v0.1.0/SHA256SUMS`. The private source revision the
binaries were built from is recorded in each tarball's `MANIFEST.txt`.
