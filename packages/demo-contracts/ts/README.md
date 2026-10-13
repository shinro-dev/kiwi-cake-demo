<!-- Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE. -->

# TypeScript types: generated, never hand-written

`generated.ts` in this directory is a build product of `../scripts/generate.mjs`. The JSON Schema files under
`../schema/v1/` are the source of truth. Do not edit `generated.ts`, and do not add hand-written contract types next
to it: a hand-written type is a second definition of the contract, and the two drift.

```bash
# from packages/demo-contracts, after `pnpm install` at the repository root
pnpm generate   # writes ts/generated.ts from schema/v1/*.json
pnpm check      # regenerates to a temporary file and diffs it against ts/generated.ts; exit 1 when stale
```

Commit `generated.ts`, so that a consumer can typecheck without running the generator; `pnpm check` is the gate that
keeps it equal to the schemas. Change a schema, run `pnpm generate`, commit both. Until the first `pnpm generate` has
run in a checkout with the dependencies installed, this directory holds only this file.

## What the types do not express

`json-schema-to-typescript` has no TypeScript form for `pattern`, `minimum`, `maximum`, `if`/`then` or `not`, and it
ignores them. So a value that typechecks can still be invalid:

- `manual_pan` with `deg: 0` or `deg: 45` typechecks; the schema refuses both;
- `run.finished` with `evidence_complete: true` and `evidence_gaps: 3` typechecks; the schema refuses it;
- a `component` with `layer: "cake_module"` and no `cake_slot` typechecks; the schema refuses it;
- any string typechecks as `SafeText`; the schema refuses one that carries a home-directory path or a dotted-quad
  address.

The types are for authoring and for reading. The schema decides validity: validate at the boundary, with the Python
validator of this package (`../python/demo_contracts`).

## How the two discriminated records come out

In the schemas, `Event` and `Command` are a closed base object plus a `oneOf` with one branch per `kind` (or per
`type`). The generator turns a schema that has both `properties` and `oneOf` into the base object intersected with a
union of the branches, so narrowing on the discriminator is the intended use. Confirm the exact printed shape against
`generated.ts` after the first `pnpm generate`; this paragraph describes the generator's documented behavior, not a
file that was inspected when it was written.

```ts
import type { Event } from '@kiwi/demo-contracts';

function describe(event: Event): string {
  if (event.kind === 'run.finished') {
    // event.payload is PayloadRunFinished here
    return `${event.payload.outcome}, evidence complete: ${event.payload.evidence_complete}`;
  }
  return event.kind;
}
```

The generator version is pinned exactly in `../package.json`. A different version may print the same types
differently, which `pnpm check` would report as stale; upgrade it in a commit of its own, together with the regenerated
file.
