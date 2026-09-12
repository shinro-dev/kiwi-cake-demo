<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Release notes

## v0.1.1

Replaces v0.1.0, which is withdrawn. The four binaries are the same bytes as
in v0.1.0: built from the same private source revision,
79b52d9dda83c65bf2fc1172314e4570ab72782c, recorded in each tarball's
`MANIFEST.txt`. What changed: the license (`LICENSE` Change Date is now
2030-09-08, so the Business Source License terms are in force;
`LICENSE-NOTE.md` explains), the documentation (a cross-machine runbook,
`docs/reproduce-end-to-end.md`; the README; `CONTRIBUTING.md`), the scripts
(the LeKiwi host wrapper `bin/pi/lekiwi_host_noninteractive.py`, the laptop
client `bin/laptop/teleop.py`, a filled `run-child.sh` example, a `VERSION`
file, one tarball instead of two), and the git history, rewritten to remove
tooling metadata from commit messages. Checksums: `releases/v0.1.1/SHA256SUMS`.

## v0.1.0

Withdrawn on 2026-09-12 and replaced by v0.1.1: its `LICENSE` carried a
Change Date already in the past. Kept here for the record.

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
