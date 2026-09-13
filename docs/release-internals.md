<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Release binary inspection

This reference is for developers inspecting the precompiled release.
It records fixture-key and binary-string details; it is not needed to
run the demo. See [release verification](verifying-a-release.md) for
download checks and [architecture](architecture.md) for signing boundaries.

## The binaries contain a fixture signing key that must never be used

`demo-plan` carries a test-fixture Ed25519 key pair used by its
`--gate-fixture-key` option. The seed of that key is derived at run time by
hashing a label that is compiled into the binary, so anyone who has the
binary can derive the private key. The demo never trusts that key: every
resident configuration this demo writes trusts only the key you generate on
your own machine, and the runner never passes `--gate-fixture-key`. Do not
use that option for anything but a throwaway experiment, and never treat that
key as a real key.

## What a strings walk of these binaries shows

The binaries are stripped, built with `panic=abort`, and every build-tree,
vendor, registry and toolchain path is remapped to one neutral token. That
resists a casual `strings` or `objdump` walk; it is not a security boundary,
and the source stays private by policy rather than by this build. A walk
still shows the following, all of it disclosed here rather than asserted away:

- A bounded set of internal literals that are functional error text a
  working binary prints by construction and that no build setting removes:
  the rule identifiers `CAKE-ABI-018` and `LK-TARGET-001`; the citations
  `Specification 10.3`, `Specification 12.4` and `Specification 14.1`; the
  published module entry symbol name `cake_module_entry_v1`; the provenance
  statement `the committed gate fixture key`; and the refusal text
  `unrecognized plan_verdict byte`. The build's own gate counts their
  occurrences per target; the count is not repeated here.
- Rust standard library paths of the form `/rustc/<hash>/library/...`, which
  the Rust project's own build bakes in and which name no file of ours.
- The neutral remap token, which reads as an absolute path that exists on no
  machine.
- The implementation identifiers `dev.shinro.lekiwi.supervisor` and
  `dev.shinro.lekiwi.supervisor.config`, which the telemetry also prints.
- The full text of the stub child script, which `demo-plan` writes out for
  segment 1, including comments that name the internal task numbers under
  which it was written.
- Provenance labels of the form `cake gate<n> T<nnn> <what>`, naming the
  internal task that introduced a key, a policy or a toolchain. They are
  inputs to digests and key derivations (the fixture key's seed is derived
  from one of them), so they are part of what the binaries compute, not
  decoration. Measured on the build this release derives from:
  `cake gate1 t011 rotation key a`, `cake gate3 T050 lekiwi supervisr`
  (quoted exactly as it appears in the binary; a pre-existing internal
  label typo, functionally inert), `cake gate3 T050 supervisor toolchain
  provenance` and `cake gate3 T051 demo plan policy`. The released
  binaries' own gate evidence is the authority for the final set, and
  this list is kept in step with it.
- The text of the embedded tested-board record, including its comment
  header, which names the board and the tools the facts were read with.
- The public half of the fixture key described above.

Nothing in the binaries names the machine or the person that built them.
`tools/strings-gate.sh` is the scan every released binary passed, with its
own nonzero control. It refuses to run without a private-tokens file
(`KC_GATE_PRIVATE_TOKENS`, one extended regular expression per line), and
the maintainer's tokens are not published, so what you can repeat on a
downloaded release is the gate's generic patterns with a tokens file of
your own; the private patterns are the maintainer's check, not yours. The
gate rejects host paths, private names and source-tree paths; it does not
reject the internal identifiers listed above, which are disclosed here
instead of being scanned for.
