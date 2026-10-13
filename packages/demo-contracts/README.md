<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# demo-contracts: the operator console contract, v1

The records that cross the boundary between the demo runtime and the operator console: events, commands and their
results, the snapshot, capabilities, components and the Cake view models. One definition, in JSON Schema, with two
consumers generated or driven from it (a Python validator, TypeScript types) and a set of synthetic fixtures.

```
schema/v1/*.schema.json   the source of truth, JSON Schema draft 2020-12
python/demo_contracts/    dependency-free validator for exactly the keyword subset the schemas use
fixtures/                 SYNTHETIC examples: valid per schema, invalid (one broken rule each) under invalid/
tests/                    unittest: fixtures, validator fail-closed behavior, command bounds, run.finished
scripts/generate.mjs      writes ts/generated.ts from the schemas (see ts/README.md)
ts/                       generated TypeScript types; never hand-written
```

## The dependency rule

- The **UI and the API consume** contracts. The **runtime emits** contracts. Nothing else is shared between them.
- This package depends on **nothing that touches hardware, the network or the robot stack**: the Python side is
  standard library only, the TypeScript side is types only. It imports nothing from `autonomy/` or `bin/`, and
  nothing in it opens a socket, a camera or a serial port.
- The arrow points one way. The runtime, the API and the UI import this package; this package imports none of them.
  Emission helpers (an event factory, sequence counters, clocks) live in the runtime, not here: a contract that ships
  its own emitter starts to describe what the emitter does instead of what the record means.
- A field that exists only in one consumer's head is not in the contract. Every object is closed
  (`additionalProperties: false`), so an undeclared field fails validation instead of riding along. That is also what
  makes "never an endpoint, a path or a credential" checkable. **Two objects are deliberately open**, and both are
  named here because an open object is a hole in that check: `snapshot.unresolved_command.evidence`, held whole so an
  operator sees what the runtime actually read, and `source#/$defs/source_counters`, whose names are the source's own.
  Neither is routed on, and what keeps them safe is the runtime's sanitizer, not this schema.

## Validate in Python

```python
import sys
sys.path.insert(0, "packages/demo-contracts/python")   # or put that directory on PYTHONPATH

import demo_contracts

errors = demo_contracts.validate("event", record)      # [] means valid
# ['$.payload.evidence_gaps: expected 0, got 2 (0 when evidence_complete is true)']

demo_contracts.CONTRACT_VERSION                          # "1"
demo_contracts.load_schema("command")                    # the checked schema object; read-only
```

`name` is a schema file's stem (`event`, `command`, `snapshot`, `capability`, `component`, `cake`, `manifest`), an
alias for a record that lives in another file's `$defs` (`command_result`, `cake_ladder`, `cake_slot`,
`cake_transaction`, `cake_update_preview`, `cake_continuity`, `tap_slot`, `preview_slot`, `history_entry`,
`run_export_index`), or a stem plus a JSON pointer (`common#/$defs/source_stamp`). A file that only holds definitions
(`common`, `source`, `history`, `export`) is refused at its root rather than answered "valid".

From a shell:

```bash
PYTHONPATH=packages/demo-contracts/python python3 -m demo_contracts event packages/demo-contracts/fixtures/event/run.finished.json
```

Tests (from the repository root; they read files only):

```bash
python3 -m unittest discover -s packages/demo-contracts/tests -t .
```

### The validator fails closed

It supports exactly: `type` (a name or a list, `null` included), `required`, `properties`, `additionalProperties`
(only `false`), `enum`, `const`, `pattern`, `minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`,
`items`, `$ref` (local `$defs` and sibling files), `oneOf`, `if` + `then`, `not`; and the annotations `$schema`, `$id`,
`$comment`, `title`, `description`. **Any other keyword raises `SchemaError` when the schema is loaded**, together with
every file it references, at any depth, including an unreferenced `$defs` entry. A validator that skipped a keyword it
did not know would answer "valid" for a rule it never evaluated. To use a new keyword, add it to the validator with
tests first.

Also refused at load: `additionalProperties` with any value but `false`, a `$ref` with sibling keywords (the TypeScript
generator would copy the target instead of naming it), a `$ref` to a URL or outside the schema directory, a pattern
that does not compile, a closed object that requires a property it does not declare, `if` without `then`, `else`.

JSON semantics Python would get wrong by default are handled: `true` is not `1`, `NaN` and the infinities are not
numbers, `2.0` is an integer, and a final `$` in a pattern does not match before a trailing newline.

## Discriminators

`event` is discriminated by `kind`, `command` by `type`. In the schema each is a closed base object (every envelope
field, `payload`/`args` typed only as an object) plus a `oneOf` with one branch per value; a branch pins the
discriminator with `const` and gives the `payload`/`args` shape. The validator recognizes this form (every branch pins
the same single property with a distinct `const`), selects the branch by the discriminator and reports only that
branch's errors. A branch may also tighten the envelope for its kind:

| kind | additionally required non-null |
| --- | --- |
| `run.started`, `run.config`, `run.finished` | `run_id` |
| `action.planned`, `action.started`, `action.completed` | `action_id` |
| `action.refused` | `action_id`, `reason_code` |
| `observation.captured` | `observation_id` |

## Rules the schemas hold

- **Every specified field is required; an absent fact is an explicit `null`.** A consumer never has to tell "not
  sent" from "not known". A property is optional only when it is an EXTENSION to a shape that was already recorded:
  optional is what keeps a producer that emits exactly the older fields valid, and absent then means "this producer
  does not record that", never zero, never false and never a value filled in from somewhere else. The full list:

  | record | optional extensions |
  | --- | --- |
  | `run.finished` | `reason_code`, `phase`, `failure` |
  | `predicate.evaluated` | `state`, `reason_code`, `rule_ref`, `rule_digest`, `unit`, `measurements` |
  | `predicate.evaluated` evidence item | `measurement`, `source_stamp` |
  | `action.planned` | `evidence_refs`, `rule_ref` |
  | `action.refused` | `evidence_refs` |
  | `command_result` | `payload_digest`, `runtime_session`, `first_seen_wall_utc` |
  | `snapshot` | `unresolved_command`, `lease`, `timings`, `run_evidence`, `execution` |
  | `component` | `cake_slot` |
  | cake `transaction` / `continuity` | `candidate_digest` / `transaction_id` |
  | `history_entry` | `data_mode`, `bundle_digest` |
  | `manifest` | `deployed`, `generation_changes` |
  | `preview_slot` | `skipped_for_mission`, `clock_relation`, `bytes_b64` |
- **A predicate says which of the four ways it has no value.** `state` is `not_evaluated` (nobody asked),
  `not_applicable` (the question does not arise here), `unavailable` (it was asked and no answer could be obtained) or
  `evaluated`; the middle two carry a `reason_code` INSIDE the payload, and `evaluated` with a null `value` is refused.
  A record without `state` keeps the older reading exactly: a non-null `value` was evaluated, a null one was not. The
  envelope's `reason_code` stays what a console routes the RECORD on. `rule_ref` and `rule_digest` are written into the
  record, so changing a threshold later mints a different digest beside the new verdict and cannot rewrite an old one.
  The time of the verdict is the envelope; the freshness of the evidence it rested on is `consumed_source_stamp`. Two
  facts, two fields, and neither is adjusted to fit the other.
- **Physical outcome and evidence completeness are separate.** `run.finished` carries `outcome` and `predicates` (what
  happened; a predicate that was not evaluated is `null`, not `false`) and, independently, `evidence_complete` and
  `evidence_gaps`. Every outcome validates with complete and with incomplete evidence. The one cross rule is
  arithmetic: `evidence_complete: true` means `evidence_gaps: 0`.
- **`data_mode` and `link` are separate.** `data_mode` is `live`, `replay` or `synthetic`; `link.state` is `connected`,
  `disconnected` or `unknown`. A disconnected live view is not replay.
- **A measured duration is never separated from what it rests on.** `snapshot.timings` groups spans by span AND clock
  domain, so a duration measured on the robot is never pooled with one measured on this machine, and every figure
  carries its `count` and the `first_seq`/`last_seq` window it was taken over. `max_ns` is the largest sample observed
  in that window, which is an observation and not a bound. A span with no sample is absent from the list rather than
  reported as zero.
- **How a command reaches the robot and what owns the robot are two facts.** `snapshot.execution.backend` is the path
  (`baseline_normal_host`, the proven controller against the motor host in its normal mode, which takes no lease of any
  kind; or `boundary_lease_host`, which needs the robot-side boundary and its command lease).
  `snapshot.execution.ownership_basis` is what actually stands between this run and anybody else commanding the robot,
  and `robot_side_enforcement` says whether anything ON THE ROBOT is refusing: `false` is a finding (nothing was asked
  for, so nothing is refusing), `null` means nothing has answered, and `true` is never claimed without a held lease.
  A console reads ownership from these and never infers it from the absence of a lease.
- **A session count is not a run count.** `snapshot.evidence` counts the whole runtime session and is never reset when
  a run starts; `snapshot.run_evidence` is one run, with `complete: null` until that run's own `run.finished` record
  states it. An absence of recorded gaps is not a complete record, so a console shows the two separately and never
  reads a silent session as a mission that went well.
- **`measured.state` names what was consumed.** It carries the joints, the base velocity and `consumed_source_stamp`:
  the exact host observation the controller read. `predicate.evaluated` carries its threshold, its measurement, its
  evidence list and, when it rests on a host observation, the same kind of stamp.
- **`provenance.reconstructed`** is `true` when a value was derived with constants that were not recorded with the
  run. Such a value is shown as reconstructed, never as recorded.
- **Commands carry the controller's bounds**, so an out-of-bounds request is refused before it reaches the controller,
  and `tests/test_command_bounds.py` reads the controller's constants to keep the two equal. The schema never widens
  a bound; the controller still enforces its own.

  | type | args |
  | --- | --- |
  | `start_cycle` | `plan_only` |
  | `observe` | `holding` |
  | `return_arm` | `plan_only` |
  | `scan_scene` | `plan_only`: a motion command, pan-only views from the saved low scan pose; `observe` stays the non-motion look |
  | `manual_pan` | `deg` (non-zero, -6..6), `holding`, `plan_only` |
  | `manual_move` | `forward_mm` (-60..60), `dz_mm` (-30..30), not both zero, `holding`, `plan_only` |
  | `manual_grip` | `action` (`latch` or `open`), `plan_only` |
  | `manual_base_pulse` | `seconds` (signed, 0.2 <= \|seconds\| <= 0.8), `holding`, `plan_only` |
  | `stop` | none: nothing but a malformed envelope can make a stop request invalid |

  `holding` and `plan_only` are explicit booleans with no default, present exactly on the commands whose behavior
  depends on them. A flag the controller would ignore is not accepted.
- **`uncertain` is a command outcome of its own**: the runtime died between accepting the command and recording the
  child, so whether anything ran is not known. `rejected` and `uncertain` always carry a `reason_code`.
- **A capability that is `disabled` or `blocked` says why** (`reason_code`), and one marked `planned` is never
  `enabled`.
- **A Python function is never a `cake_module`.** `component.layer` is one of `logical_python`, `process`, `device`,
  `cake_module`; a `cake_module` must name the resident slot that backs it (`cake_slot`), and no other layer may.
- **Free text is tripwired.** `safe_text` fails validation when it carries a home-directory path or a dotted-quad
  address; `named_value` (the `run.config` summary, action parameters, perception measurements) restricts string
  values to a character set that cannot spell a path, a URL or an endpoint. `provenance.model_identity` names a model
  (a registry id such as `org/name@revision`) and never where it was loaded from: its character set has to admit `/`,
  `:` and `@`, so a URL, a home-directory path or a dotted-quad address is refused by the same kind of tripwire. The
  emitter sanitizes; the schema catches the emitter that did not. This is a tripwire, not a proof of absence: it does
  not recognize a hostname or a user name, and it cannot tell a relative path from a registry id.
- **Identifiers are opaque.** `id` is `^[A-Za-z0-9_.-]{1,64}$`, minted by the runtime: never a hostname, a robot id, a
  camera id or a device serial. A camera is named by role (`wrist`, `front`).

## The runtime surfaces this contract also describes

Four objects describe things the runtime already produced and nobody had written down, so a console had to keep a
second, hand-maintained copy of each. They are generated from here now, and `tests/test_runtime_surfaces.py` PARSES the
runtime's own source and compares the two field for field, so the contract cannot drift from the producer unnoticed.

- `source#/$defs/tap_slot` and `source#/$defs/preview_slot`: what each read-only source is doing right now. A slot is a
  LIVE surface, not a recorded record. Only a `connected` source reports values; every other state is null values plus a
  reason code. **The age of the picture cannot be computed**: the capture clock is the source's and the receive clock is
  this machine's, so `capture_age_ms` is null and `capture_age_reason` says why, and `common#/$defs/clock_relation` is
  the only thing that would ever license computing it. Nothing measures one today, so it is null everywhere.
- `history#/$defs/history_entry`: one finished run, as the command journal folded it. Every measured field is nullable
  because an older or a truncated completion record carries less.
- `manifest.schema.json`: what a run states about itself, matching the dict the runtime already writes beside each run.
  The code identity is a pinned content-addressed bundle built from a STATIC import closure, so a module reached by a
  computed name is not in it and the manifest does not claim otherwise.
- `export#/$defs/run_export_index`: the index of a run bundle that can be handed to somebody else. It is read-only and
  BOUNDED, it packages only what the runtime already keeps, and an image or a summary it could not include is a stated
  gap on the index rather than a failed export. Nothing produces one yet: this is the shape, ahead of the producer.

## Cake view models

`cake.schema.json` takes its nouns (resident, capsule, Plan, slot, generation, epoch, transaction, preview, rollback)
from `docs/autonomous-demo/cake-capability-assessment.md`. From `docs/autonomous-demo/cake-runtime-contract.md` it
takes two rules rather than vocabulary: do not create a fake Cake ABI, and expose only safe evidence fields (short
content identities, session and module identity, an admission result, a transition duration), never a local path or a
machine identifier. The five ladder rung names are this contract's own; neither document defines them. These are
**view models for a console, not a Cake ABI**: `lifecycle_state`, `phase` and `native_events[].code` are tokens passed
through from the resident, deliberately not enums, because the resident's own interface is authoritative and this
contract does not invent its vocabulary.

- `ladder`: five rungs (`artifacts_available`, `resident_reachable`, `admin_interface_compatible`, `modules_active`,
  `mission_participation`), each `{value: true | false | null, evidence}`. A higher rung never implies a lower one
  was checked. Today every rung is `false` or `null`.
- `update_preview` is bound to `candidate_digest` + `plan_digest` + `epoch`; a console discards it when any of the
  three changes.
- `transaction`: `committed` and `durable` are separate facts; a rollback is itself a forward transaction.
- `continuity`: `sources[]` is non-empty and every source declares `outside_replaced_module: true`: a module cannot
  testify to its own continuity.
- Every Cake object carries `synthetic`. Nothing connects the console to a resident today, so every fixture says
  `synthetic: true`.

## Fixtures

All fixtures are synthetic and hand-authored with reserved values (`synthetic-run-0001`, all-zero digests,
`2026-01-01T00:00:00Z`, documentation addresses only inside the invalid fixtures that prove an address is refused).
No frame, log, perception output or value from a robot is in this package, and none may be added.

A fixture's schema is the name of its directory (`fixtures/event/`, `fixtures/cake_ladder/`, ...). There is one valid
example per event kind, per command type and per command_result status. `fixtures/invalid/<schema>/` holds records
that each break one rule, and `fixtures/invalid/expectations.json` maps each to a fragment of the error it must
produce, so an invalid fixture cannot pass by failing for some other reason.

## TypeScript

`pnpm generate` writes `ts/generated.ts` from `schema/v1/*.json`; `pnpm check` regenerates to a temporary file and
diffs. See `ts/README.md`, including what the types cannot express.

## Contract version policy

- The contract version is the **major version only**: `"1"`. It appears as `schema_version` on every event, as
  `contract_version` on commands, snapshots and producers, as the directory `schema/v1/`, as `CONTRACT_VERSION` in
  Python, and as the major version of `package.json`.
- Every record pins it with `const`. A v1 consumer **refuses** a record of any other version instead of guessing, and
  a runtime refuses a command from a client that speaks another version (a `rejected` command_result with a
  `reason_code`).
- **Within v1, no change may invalidate a record that was valid, or change the meaning of a field.** Because every
  object is closed, even adding an optional field makes new records invalid for an older validator: so producer and
  consumers of one deployment always run the same checkout of this package, and an additive change (a new optional
  field, a new `$defs` entry) bumps the minor version of `package.json` together with fixtures and tests.
- **Anything else is v2**: removing or renaming a field, adding a required field, adding or removing an enum value
  (a new event kind included, since a v1 consumer refuses a kind it does not know), tightening or widening a bound,
  changing a unit or a meaning.
- **One recorded exception: a new command type that is not a Cake command.** `scan_scene` (1.7.0) was added within v1
  by docs/autonomous-demo/overnight-plan-2026-09-26.md (track P0b). A command travels from a console to the runtime of
  the same checkout, a runtime whose schema lacks a type refuses the command as invalid before anything runs, and the
  records that name the type afterwards are read by consumers of that same checkout. The fixture test pins the number
  of event kinds (19), not of command types; a new event kind stays v2. v2 is a new directory `schema/v2/` next to `schema/v1/`; v1 files are not edited into
  v2.
- A recorded run stays readable: the schema directory of the version it was recorded with stays in the repository.
