<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Private Cake requirement: mobile_approach hosting

This document describes required **behavior only**. It names no private
implementation detail, file, internal type or path. It is written so it can
be handed to the private Cake source repository's maintainers as a scoped
ask, and so it can stay in this public branch without leaking anything about
how the private side is built. The inspection that produced it is
summarized in `cake-capability-assessment.md`.

Status: **small private extension, optional for the first demo.** The
runtime already supports multi-slot Plans, minimal-closure live replacement
of one slot while another slot's child process runs on untouched, typed
refusal of an invalid candidate before any effect, known-good preservation,
rollback and coherent introspection. The demo can run on that today with a
pre-declared capability slot (Path A in the assessment). What follows is
what the stronger "a signed update arrives and is verified live" beat needs
(Path B), plus the small build-side items both paths need.

## Required for Path B (runtime admission)

Cake must be able to admit a candidate capsule presented at runtime,
through the same verification it applies to a capsule at start (signature
policy against the configured trusted keys, content identity, descriptor
checks, ELF and ABI policy, target-profile facts), such that:

- a tampered, unsigned or untrusted candidate is refused with a typed
  reason, recorded as an event, with no effect on the running graph;
- an admitted candidate becomes eligible as the replacement target of an
  existing slot through the existing replacement operation;
- the admission is idempotent under a repeated operation identity, as the
  other mutating operations already are.

It must also be able to hold a new immutable configuration content
presented the same way, or the demo will pre-stage that content through a
holder slot declared at start (the assessment describes this pattern).

## Required for both paths (build-side, no engine change)

- A second build identity of the process-supervisor module for the
  `mobile_approach` slot, because that module is single-instance per
  capsule identity.
- An inert build variant of the same module that accepts the same
  configuration schema and spawns nothing, serving as the "capability
  absent" generation of the pre-declared slot.
- A test that a replacement transaction targeting a process-supervising
  slot stops its child during quiescence, spawns the new generation's child
  after activation, and leaves the other supervising slot's child
  uninterrupted. The drain barrier and the module's own quiesce path are
  each measured today; their composition on a supervising member is not.

## Not required

Cake does not need to:

- add or remove a slot at runtime (the capability slot is pre-declared);
- hand a resource grant or a child process over across generations
  (`mobile_approach` is stateless and its child restarts on replacement);
- understand robot actions, motor commands, velocity or acceleration bounds;
- interpret camera frames, perception output or VLA state;
- provide or mediate a high-frequency data channel between the capability
  and anything else; the capability's operational data flows over a
  transport Cake does not participate in;
- provide any actuator safety guarantee; that stays with the existing stable
  robot-command path, unchanged by this work;
- push lifecycle notifications; polling the slot listing, health and flight
  ring is sufficient for the task executive and the evidence UI.

## Scope discipline

Path A needs no engine change and is the committed baseline. Path B fills an
operation the protocol already reserves with pieces that already exist; it
is pursued in parallel and swapped in only when its own gate is green.
Nothing broader (a general plugin system, a public module SDK, arbitrary
slot counts, runtime slot addition) is requested by this document.
