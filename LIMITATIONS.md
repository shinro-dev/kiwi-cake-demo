<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Limitations

This is a demo released as-is. Everything below is a limit of what this
release does and shows. There is no roadmap in this repository, and nothing
here should be read as a promise about a later release.

## Actuator-safe recovery is not guaranteed

After the resident is killed and relaunched, Cake restores its declared state
exactly: the Plan digest, the configuration identity, the build identity and
the target-profile digest read back identical, and a fresh session identity
is reported. That is what the demo record measured and what this demo shows.

What it does not show is the actuators coming back disarmed. In this demo the
motors are owned by the stock LeRobot host, which enables torque on every
successful connect, recovery included. Cake's admin protocol carries no
torque or actuator concept, so there is nothing for Cake to gate. The
operator's verdict in the demo record reads, with two internal ledger
references removed at the marked elisions:

> Cake-level recovery PASS ([...] byte-identical declared authority),
> actuator-safe-recovery NOT DEMONSTRATED ([...] the LeRobot child re-arms
> unconditionally on connect).

Treat every restart in segment 2 as a torque-on event. `SAFETY.md` is
mandatory for that reason.

## No live module replacement

The public demo does not perform a live replacement of a running module.
The demo shows admission, supervision, recovery and telemetry, and nothing
else.

## The relaunch is a new process, not a resumed one

A relaunched resident re-activates the Plan its configuration names.
Module memory does not survive it: the supervisor's restart ordinal begins
again at zero, as the record's crash-recovery beat shows. Whether declared
persistent resources survive a replacement is a property of the runtime
that this demo does not exercise. "A fresh session identity" is a comparison
between two status answers, not a description of the new process.

## What is signed, and what is not

The signed object is the capsule (the program package). The Plan artifact
format carries no signature; the Plan is bound to the capsule by content
identity, which is exactly the binding the tamper beat breaks by changing one
byte of the stored object. Under the `require` signature policy the resident
refuses an unsigned capsule and a capsule signed by a key it does not trust,
naming the policy word; that refusal is exercised by the demo's own verify
step and was measured on the development host, not on the board.

## No timing figure is a claim

Nothing in this demo, its transcripts or these documents states a latency, a
duration or a rate. Restart backoff and the termination deadline are
configured waits, not measurements.

## The safe-stop command is yours

The supervisor runs a configured command once for every child exit it
observes. The demo record proves that it ran; it does not record what the
operator's command did. This release ships only a template that writes one
log line. Anything that touches the robot is yours to write and to test on a
stand first. The wheels stop when the host stops because the host stops
them, not because Cake does.

## The preflight cannot reach PASS in the public build

`demo-preflight` runs seven checks in order: page size, glibc, memfd exec
policy, admission, load probe, observer, shipped binaries. The sixth check
verifies a module that the board builds for itself from a source tree that
is not part of this release, so in the public build it always refuses with
the text `--observer-module was not given`, and the seventh check never
runs. The demo runner requires the first five checks to print their green
lines and accepts exactly that one refusal as the end of the roster; any
other refusal stops the run. `bin/doctor.sh` re-implements the seventh check
in shell (one no-op run of each shipped binary) and labels it as such.

## The tested board record is exact

`demo-preflight` compares the running machine against one embedded record:
Raspberry Pi 5, Raspberry Pi OS based on Debian 13 (trixie), a 16 KiB page
kernel, glibc 2.41. The page-size check requires equality with 16384, and the
glibc check requires the running version to be at or above 2.41 as well as at
or above the binaries' own floor of 2.34. Consequences, by construction:

- A Raspberry Pi 4 is refused at the page-size check (its kernels use 4 KiB
  pages).
- A Raspberry Pi 5 running Raspberry Pi OS bookworm (glibc 2.36) is refused
  at the glibc check.
- A Raspberry Pi 5 booted with the 4 KiB kernel (`kernel=kernel8.img`) is
  refused at the page-size check.

`docs/targets.md` says what each refusal means and what the
`--unsupported-target` override does and does not allow. There is no x86-64
build in this release: the compiled-in target profile is the aarch64 LeKiwi
profile, so an x86-64 resident refuses its own package before anything runs.

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
own nonzero control, so anyone can repeat it on a downloaded release. The
gate rejects host paths, private names and source-tree paths; it does not
reject the internal identifiers listed above, which are disclosed here
instead of being scanned for.

## Provided for evaluation

The binaries are provided for evaluation only, without source and without
warranty of any kind, under the Business Source License 1.1 in `LICENSE`.
