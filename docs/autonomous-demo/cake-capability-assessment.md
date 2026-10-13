<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# Cake capability assessment for mobile_approach

Read-only inspection of the private Cake source repository, performed to
answer one question: can the current Cake runtime independently admit,
activate, replace, reject and introspect a second long-lived supervised
capability process while another protected process stays alive and
unaffected? Everything below is stated behaviorally. It names no private
file, path, commit, task identifier or implementation detail beyond the
concepts the public documentation of this repository already uses
(resident, capsule, Plan, slot, binding, resource grant, admin socket,
flight record, supervisor module).

Nothing was built, run, modified or committed in the private repository.

## 1. Verdict

**Small private extension.** The architecture already supports independent
capabilities with minimal-closure live replacement, rejection before any
effect, known-good preservation, rollback and coherent introspection. One
narrow surface is missing for the demo as framed: **admission of a new
capsule at runtime.** Today a capsule is admitted (signature, content
identity, descriptor, ELF policy, ABI range, target-profile facts) only for
the slots the Plan names at resident start, and a live replacement can only
move a slot onto a capsule and a configuration content the process already
holds. The administrative protocol reserves an operation for runtime
admission; it is not implemented.

Two ways forward exist, and they share one Plan shape, so the robotics work
is the same under either:

- **Path A, no engine change:** pre-declare the `mobile_approach` slot at
  start in an inert generation, pre-admit the real build at start through a
  second holder slot, and switch live with the existing replacement
  operation. Cake performs the replacement; a candidate outside the
  admitted set is refused before any effect. The "a signed update arrives
  and is verified live" beat is not shown; signature and tamper refusals are
  shown at start, as the public demo already does.
- **Path B, the small extension:** implement runtime admission over the
  existing admission path. The candidate is then verified live, a tampered
  candidate is refused live with the running graph untouched, and an
  admitted candidate becomes eligible for the same replacement operation.

## 2. Behavior currently available

**Topology.** One resident hosts a graph of named slots (module instances)
with typed bindings, declared resource grants and lifecycle edges. Multi-slot
Plans run today on both the x86-64 development host and the board. Modules
are in-process, sealed, signed capsules; the process-supervisor module is a
capsule whose job is to spawn and supervise one external child process
(today, the LeRobot host), with a bounded restart policy, a configurable stop
signal, an optional post-exit safe-stop command, and a parent-death signal so
no child outlives the resident. The child's command, working directory,
environment and log bound are immutable configuration content named by the
Plan by content identity.

**Live replacement.** A replacement is a transaction with a total state
machine, a preview that reports the affected closure before anything moves,
an epoch-guarded drain barrier, atomic binding-snapshot publication, reverse
activation-order teardown and reclamation only after every guard has exited.
The affected closure is computed by declared binding-change policy, not by
graph reachability, so a slot nothing is bound to is never quiesced by
another slot's replacement. This is measured: the existing demo replaces a
non-actuator slot over the real admin socket while the supervisor slot stays
at its generation with the same child process running throughout.

**Rejection before effect.** A candidate that names a capsule the process has
not admitted, a configuration content it does not hold, a configuration that
does not fit the incoming module, or a change the engine does not execute is
refused on the way into the validated state: no member is quiesced, no module
is called, the Plan digest and epoch are unchanged, and the refusal is a
typed class carrying no host detail. A candidate that passes validation but
fails inside a module callback is torn down without publication, leaks
nothing, and leaves the prior Plan authoritative. Rollback is itself a new
forward transaction onto the prior durable Plan.

**Admission at start.** Signature policy (allow, warn, require) against a
trusted key set, content identity re-hashed on every store read, descriptor
checks, ELF import and constructor policy, ABI version-range checks and
target-profile facts. A tampered store object refuses the start naming the
content identity; an unsigned or untrusted capsule is refused naming the
policy. Two slots may select one capsule only if that module declares
multi-instance support; the process-supervisor module is single-instance,
so a second supervised process needs a second build of that module with its
own content identity (a build-matrix item, not an engine change).

**Introspection.** Over the local admin socket, all polled, none pushed:
status (session, build, configuration, Plan digest, epoch, transaction
phase), the active Plan, slots (identity, name, capsule and configuration
identities, generation, lifecycle state), bindings, resources, hardware
facts, per-slot health read from the modules' own health exports, and the
bounded flight ring of typed event records with sequence cursors. Transaction
transitions, closure membership, drain resolution, publication, reclamation,
candidate failure, plan refusal, admin refusals and replays, and every
supervisor child start, exit, restart-bound and health change are recorded
as events. No wall-clock timing appears anywhere by design.

**AMD64 parity.** The x86-64 development host is where the private gate runs
every build, including the supervisor recovery sequence and the live
replacement scene, through a development driver that shares the shipped
resident's code path and differs only in taking the host's architecture
profile (the shipped resident enforces the ratified AArch64 profile). A
public-release build recipe already produces all four public binaries for an
x86-64 row; this public repository does not yet publish that row. Suitable
for development as is. The local binaries found on the development machine
predate the private repository's current development tip and should be
rebuilt from it before use; that tip also carries a fix for a shutdown fault
affecting capsules with their own thread, which the supervisor module is.

## 3. Behavior missing

- **Runtime admission of a new capsule (and its configuration content).**
  Admission runs only at start for the start Plan's slots; a live candidate
  must name a capsule and a configuration already held. The protocol
  reserves an operation for this; it answers "unavailable" today.
- **Adding or removing a slot at runtime.** Both are computed as planned
  actions and refused, typed, before any effect: the slot set is fixed at
  start. Not needed if the capability slot is pre-declared.
- **Resource-grant handover across a replacement.** Deferred by design. Not
  needed: a replacement that keeps the slot's grant set unchanged hands the
  incoming generation the same data directory.
- **Child-process handover across generations of a supervising slot.**
  Deferred by design. Not needed: `mobile_approach` is stateless and its
  child is meant to restart on replacement.
- **Push notification of lifecycle changes.** Not present; polling the
  listing, health and flight ring is the surface.

## 4. Minimum private change

Behavioral requirement, if Path B is chosen (see
`private-cake-requirement.md` for the standalone statement):

> Cake must be able to admit a candidate capsule presented at runtime
> through the same verification it applies at start (signature policy,
> content identity, descriptor, ELF and ABI policy, profile facts), refusing
> a tampered, unsigned or untrusted candidate with a typed reason and with
> no effect on the running graph, and making an admitted candidate eligible
> as a replacement target for an existing slot. It must also be able to
> hold a new configuration content presented the same way, or the demo
> pre-stages that content through a holder slot.

Under Path A no engine change is required. Both paths still need, on the
private side: a second process-supervisor build identity for the
`mobile_approach` slot, a small inert build variant of the same module that
accepts the same configuration schema and spawns nothing (the "absent"
generation), and a test that a replacement transaction targeting a
process-supervising slot stops its child during quiescence and spawns the
new generation's child, with the other supervisor's child uninterrupted.
That last property is not yet measured for a supervising member; the drain
barrier and the module's own quiesce path are each measured separately.

## 5. Evidence coverage

Properties with private automated tests today, stated only as yes, no or
partial:

- multiple independently supervised slots in one resident: **yes**
- two instances of one implementation from distinct configurations: **yes**
- one slot replaced while an unrelated slot keeps every callback and thread
  work unit: **yes**
- one slot replaced while the process-supervisor's child stays the same
  process: **yes** (non-actuator target; the supervisor is never the target)
- replacement transaction targeting a process-supervising slot: **no**
- candidate naming an unadmitted capsule refused before any module effect:
  **yes**
- candidate failing inside a module callback torn down, prior Plan
  authoritative, no leaked handle or region: **yes**
- tampered store object refused naming the content identity: **yes** (at
  start)
- unsigned and untrusted-key capsules refused under the require policy:
  **yes** (at start and by the verify tool)
- live admission of a tampered candidate refused with the graph untouched:
  **no** (no runtime admission exists)
- rollback lands on the previewed prior durable Plan: **yes**
- duplicate operation identities replayed, never executed twice: **yes**
- stale Plan-digest or epoch preconditions refused: **yes**
- slot listing, health and flight reads coherent within one epoch, refused
  rather than mixed: **yes**
- supervisor child killed and restarted under the next ordinal, exit status
  carried in the event: **yes**
- resident killed and relaunched with a fresh session and no orphan child:
  **yes**
- ordinary shutdown drains an owned-thread capsule before unmapping it:
  **yes** (on the current private development tip)
- the same behaviors on x86-64: **yes** (the gate runs there); on the board:
  **partial** (supervisor recovery and admin reads recorded on the board;
  live replacement on the board gated on a separate race record)

## 6. Impact on demo schedule

- Path A, no engine change: roughly one to two engineering days on the
  private side (two build identities, the inert variant, the supervising-
  slot replacement test, plus that repository's evidence and review
  discipline). Robotics work can start immediately against the pre-declared
  Plan shape.
- Path B, runtime admission: an additional two to four engineering days on
  the private side. The admission function, the content store, the policy
  evaluation, the idempotency ledger keyed by operation identity and the
  reserved wire code already exist; the work is the dispatch arm, registering
  the new mapping and content with the live graph, the typed refusals, the
  events, and the tests. It does not block the robotics work because the
  Plan shape and the replacement call are the same under both paths.
- Rebuilding the desktop binaries from the current private tip: hours.

Both fit inside a 7 to 10 day window if Path A is the committed baseline and
Path B is pursued in parallel and only swapped in when its gate is green.

## 7. Recommended next step

Adopt Path A as the baseline design now: pre-declare `mobile_approach` in the
start Plan with an inert generation, pre-admit the real build through a
holder slot, and drive availability with the existing replacement operation.
Open the small private extension for runtime admission in parallel, scoped
exactly to section 4, and narrate the demo honestly under whichever path is
green on demo day. Then rebuild the desktop binaries from the current
private development tip and proceed to the `mobile_approach` implementation
against `mobile-approach-spec.md`.

## Adversarial check

- **Would an investor be seeing Cake perform the admission and the
  replacement?** The replacement, yes: a real transaction over the admin
  socket, with the affected closure previewed and the other supervisor's
  child untouched, is what the runtime does. The admission: under Path A it
  happened at start, and the live beat is a Cake replacement among admitted
  signed builds plus a Cake refusal of an unadmitted candidate; under Path B
  the admission itself is live. The narration must say which.
- **Is rejection enforced by Cake?** Yes. The typed refusals come from the
  runtime before any module is called, and the signature and content-identity
  refusals come from the admission path, not from application code.
- **Is the protected robot session preserved?** Yes for the LeRobot
  supervisor slot, measured today when another slot is replaced. It must be
  re-measured with `mobile_approach` as the target, which is one of the
  tests section 4 asks for.
- **Could this be accused of being importlib.reload behind a Cake UI?** Not
  if the capability is bound to a Cake generation: capsule identity plus
  configuration identity, either of which changing is a new generation the
  runtime creates. The remaining honest gap is the same one the public
  documentation already states for the LeRobot host: the child program's
  bytes are outside the capsule signature. An optional hardening is to pin
  the child program's content digest in the immutable configuration and have
  the supervising module refuse to spawn on a mismatch, labelled as a
  module-level check rather than a Cake admission property.
- **Is the private change disproportionate?** No. Path A needs no engine
  change; Path B fills a reserved operation with existing pieces.
- **Can the 7 to 10 day target still be hit?** Yes, with Path A as the
  committed baseline.
