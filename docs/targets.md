<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Supported targets

All binaries are built for a glibc floor of 2.34 and depend on `libc.so.6`
alone (the x86-64 loader entry aside). The floor is 2.34 because it is the
first glibc that merges `libpthread` into `libc`; below it a second library
dependency appears that the target profile refuses.

| Row | Target | Binaries | What `demo-preflight` does | Segment 1 | Segment 2 |
| --- | --- | --- | --- | --- | --- |
| Pi 5, tested | Raspberry Pi 5, Raspberry Pi OS based on Debian 13 (trixie), 64-bit, default kernel (16 KiB pages), glibc 2.41 | `pi5-aarch64` | five checks green, then the documented observer refusal | yes | yes |
| Pi 5, other OS | Raspberry Pi 5 on bookworm (glibc 2.36), or any Pi 5 booted with `kernel=kernel8.img` (4 KiB pages) | `pi5-aarch64` | refuses at glibc or at page size, by construction | `--unsupported-target` only | no |
| Pi 4 | Raspberry Pi 4, any 64-bit OS | `pi5-aarch64` (the only tarball; v0.1.0 also published the same bytes as `pi4-aarch64`) | refuses at page size, by construction | `--unsupported-target` only, untested | no |
| x86-64 | Ubuntu or Debian desktop, the SO-101 desktop case | none in this release | refuses at page size; the resident refuses its own package | no | no |

## Why the preflight refuses what it refuses

`demo-preflight` carries one embedded record of the board the demo was run
on: Raspberry Pi 5 Model B, Raspberry Pi OS trixie, kernel with 16 KiB pages,
glibc 2.41. Its first two checks compare the running machine against that
record:

- `page-size` requires `getconf PAGE_SIZE` to equal 16384 exactly. Raspberry
  Pi 4 kernels use 4096, and so does a Pi 5 booted with `kernel=kernel8.img`.
- `glibc` requires the running glibc (the last field of `ldd --version`'s
  first line) to be at or above the record's 2.41 and at or above the
  binaries' floor of 2.34. Bookworm ships 2.36.

Both refusals say what they found and what the record expects. They are not
a judgment about whether the binaries would run; they are a statement that
the board is not the tested one. The preflight has no override of its own.

The remaining checks read facts of the board that only the board can answer:
`memfd-noexec` refuses only when `vm.memfd_noexec_scope` is 2; `admission`
runs the package you just built through the same admission call the resident
runs at start; `load-probe` maps the admitted module through the sealed
loader path and resolves its entry symbol without executing any module code.
The `observer` check and the `shipped-binaries` check are described in
`LIMITATIONS.md`: the first cannot pass in the public build and the second
therefore never runs, so `bin/doctor.sh` performs an equivalent of the second
in shell.

## What `--unsupported-target` does

On a machine `bin/doctor.sh` classifies as anything other than the tested
Pi 5, `bin/demo-segment1.sh --unsupported-target` skips `demo-preflight` and
runs the doctor's own checks instead: architecture, glibc at or above 2.34,
`vm.memfd_noexec_scope` not 2, and one no-op run of each shipped binary.
The resident's own admission still runs at start, so a package the board
cannot admit is still refused, just later and with less explanation. The
transcript records that the preflight was skipped and why.

The flag is rejected on the tested Pi 5 (it is not needed there) and it is
never honoured by `bin/demo-segment2.sh`: nothing that moves a robot runs on
a board the preflight has not accepted.

## What `bin/doctor.sh` exits with

| Exit | Meaning |
| --- | --- |
| 0 | the tested board, every shipped binary starts, and with `--capsule` the preflight accepted the package |
| 3 | the board is not the tested one; informational, segment 1 may still run with `--unsupported-target` |
| 4 | a shipped binary did not start under this board's dynamic loader, or `demo-preflight --list-checks` failed |
| 5 | no release binaries were found, or usage |
| 6 | with `--capsule`: `demo-preflight` refused at a named check; the doctor explains which and why |
| 7 | with `--capsule`: `demo-preflight` output could not be parsed, for example because the process died on a signal (exit 128 plus the signal number); the reason is printed after the raw output |

When more than one applies, the first in this order wins: 5, 4, 6, 7, 3, then 0.

## The x86-64 row

Every binary, on every target, carries the same compiled-in target profile:
the aarch64 LeKiwi profile with 16 KiB pages. On an x86-64 machine the
resident refuses the package it just built because the Plan names a
target-profile digest that is not the active profile's, before any module is
mapped, and `demo-preflight` refuses at the page-size check. The x86-64
build can therefore run only its usage and `--list-checks` output, which is
not a demo. No x86-64 tarball is published in this release.

## What was measured where

The tested-board facts and both demo segments were captured on the Pi 5 row.
The Pi 4 row and the other-OS Pi 5 row were not exercised by anyone; they are
provided as-is because the tarball is the same file and the ELF facts say its
binaries should load, which is not the same as having run them.
