<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Preflight transcripts

Recorded `demo-preflight` output used by `tests/test-preflight-classify.sh`:

| Fixture | Origin |
| --- | --- |
| `preflight-five-green-observer` | the operator's own run of the preflight on the tested Pi 5 board, recorded outside the evidence record (five green checks, observer refused), paths removed |
| `preflight-page-size` | an x86-64 machine with 4096-byte pages, recorded |
| `preflight-glibc` | a glibc refusal composed from the binary's own message format |
| `preflight-observer-without-green` | a constructed control: the observer refusal without the five check lines, which must not be accepted |
