// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The event ENVELOPE, composed from the generated contract types.
//
// Why this file exists: `@kiwi/demo-contracts` generates `Event` from a schema that is a closed base object (the
// envelope: schema_version, seq, producer, t_mono_ns and the rest) plus a `oneOf` over `kind`. The generator emitted
// only the `oneOf` branches, so the generated `Event` carries `kind`, `payload` and the per-branch id hints and NONE
// of the envelope. See packages/demo-contracts/ts/README.md, which describes the intersection the generator did not
// print. Every field type below is imported from the generated module; the only thing stated here is which fields the
// envelope has, because nothing generated says it. Nothing else in this application restates a contract shape.
//
// The four ids appear in both halves with different nullability (`run_id?: Id` in a branch, `nullable_id` in the
// envelope), so they are removed from the branch side and taken from the envelope, which is the required form.

import type {
  ClockDomain,
  ContractVersion,
  Event as EventBody,
  Id,
  NullableId,
  NullableReasonCode,
  NullableWallUtc,
  Producer,
  Provenance,
} from '@kiwi/demo-contracts';

/** `Omit` over a union, member by member, so the `kind` discriminator survives. */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

type BranchOnlyKeys = 'run_id' | 'action_id' | 'observation_id' | 'reason_code';

/** The envelope half of the event schema: every field required, an absent fact an explicit null. */
export interface EventEnvelope {
  schema_version: ContractVersion;
  seq: number;
  run_id: NullableId;
  action_id: NullableId;
  observation_id: NullableId;
  producer: Producer;
  source_session: Id;
  t_mono_ns: number;
  clock_domain: ClockDomain;
  t_wall_utc: NullableWallUtc;
  reason_code: NullableReasonCode;
  provenance: null | Provenance;
}

/** One contract v1 event as it travels: envelope plus the branch for its `kind`. Narrow on `kind`. */
export type ContractEvent = DistributiveOmit<EventBody, BranchOnlyKeys> & EventEnvelope;

/** The event kinds this application folds, as a value (the generated `EventKind` is a type only). */
export const EVENT_KINDS = [
  'run.started',
  'run.config',
  'run.finished',
  'state.transition',
  'action.planned',
  'action.started',
  'action.completed',
  'action.refused',
  'measured.state',
  'observation.captured',
  'perception.result',
  'predicate.evaluated',
  'stop.requested',
  'stop.acknowledged',
  'stop.outcome',
  'timing.span',
  'evidence.gap',
  'telemetry.dropped',
  'diagnostic.text',
] as const satisfies readonly ContractEvent['kind'][];

const KIND_SET: ReadonlySet<string> = new Set<string>(EVENT_KINDS);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * A structural check at the boundary, not a validator. The schema decides validity and the runtime validates before
 * it sends; this only refuses a line the reducer could not fold at all, so one malformed line cannot take the screen
 * down. A line that fails here is reported as a stream defect, never dropped in silence.
 */
export function isContractEvent(value: unknown): value is ContractEvent {
  if (!isRecord(value)) return false;
  if (value['schema_version'] !== '1') return false;
  if (typeof value['seq'] !== 'number' || !Number.isFinite(value['seq']) || value['seq'] < 0) return false;
  if (typeof value['kind'] !== 'string' || !KIND_SET.has(value['kind'])) return false;
  if (!isRecord(value['payload'])) return false;
  if (typeof value['t_mono_ns'] !== 'number') return false;
  if (!isRecord(value['producer'])) return false;
  return true;
}

/** The narrowed event for one kind, for panels that take a single kind. */
export type EventOf<K extends ContractEvent['kind']> = Extract<ContractEvent, { kind: K }>;
