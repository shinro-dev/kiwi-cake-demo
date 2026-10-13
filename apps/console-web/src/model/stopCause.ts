// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE STOP'S OWN CAUSE, JOINED BY ACTION ID (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 6.3.4; audit C4).
//
// On 2026-09-27 the failure banner showed a false `straddle_far_edge_row` (333 px against 420 px, no action id, 31 s
// before the stop) and its frame as the cause of a stop whose own record was a refused CLEARANCE lift: base velocity not
// confirmed zero, 0 of 3 reads, 1.17 deg/s. The controller left `failure.measurements` empty for that code, so the
// console joins the run's own records instead, by the refusal's `action_id` and nothing else:
//
//   the newest `action.refused` of the run whose reason code is the run's; its `action_id` selects the false
//   `predicate.evaluated` records, the newest `measured.state` and the `action.planned` label with the same `action_id`
//   and a lower `seq`.
//
// When no refusal matches, `failure.measurements` are shown as recorded; when those are empty, the record carries no
// measurement for this stop and the words say exactly that. No predicate of another action is ever shown as the cause.

import type { ContractEvent } from '../contracts/envelope';
import { utcStamp } from './utcTime';

export interface StopCause {
  run_id: string | null;
  reason_code: string;
  /** The run's own sentence for its end (`failure.detail`), or null when it wrote none. */
  detail: string | null;
  refusal: { action_id: string; seq: number; label: string | null; action_type: string | null; detail: string | null } | null;
  /** The sentences, in order: the refusal, each false predicate of the same action, the base velocity in its reading. */
  lines: string[];
  /** The gripper reading of the joined measured state, and when it was recorded; null without one. */
  gripper: number | null;
  measured_at: string | null;
  /** The frame the failure itself names (`failure.observation_ids[0]`), and no other. */
  frame_id: string | null;
  /** Every frame the failure names (`failure.observation_ids`), in its order; empty when it names none. */
  frame_ids: string[];
  /** How many predicates the run recorded, and how many of them are tied to the stop by the join. */
  predicates_recorded: number;
  predicates_tied: number;
}

export const NO_STOP_MEASUREMENT = 'The record carries no measurement for this stop.';

type Payload = Record<string, unknown>;

function payloadOf(event: ContractEvent): Payload {
  const payload = (event as { payload?: unknown }).payload;
  return typeof payload === 'object' && payload !== null ? (payload as Payload) : {};
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** A recorded number as the screen says it: rounded to two decimals, as recorded otherwise (spec rule 5.7.3). */
export function recordedValue(value: unknown): string | null {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  const number = finite(value);
  if (number === null) return null;
  const rounded = Math.round(number * 100) / 100;
  return String(Object.is(rounded, -0) ? 0 : rounded);
}

function fixed(value: number, digits: number): string {
  const out = value.toFixed(digits);
  return /^-0\.?0*$/.test(out) ? out.slice(1) : out;
}

/** "x 0.000 m/s, y 0.003 m/s, rotation 1.17 deg/s": m/s to three decimals, deg/s to two, as recorded. */
export function baseVelocityWords(velocity: unknown): string | null {
  if (typeof velocity !== 'object' || velocity === null) return null;
  const v = velocity as Payload;
  const x = finite(v['x_mps']);
  const y = finite(v['y_mps']);
  const theta = finite(v['theta_dps']);
  if (x === null && y === null && theta === null) return null;
  const parts: string[] = [];
  if (x !== null) parts.push(`x ${fixed(x, 3)} m/s`);
  if (y !== null) parts.push(`y ${fixed(y, 3)} m/s`);
  if (theta !== null) parts.push(`rotation ${fixed(theta, 2)} deg/s`);
  return parts.join(', ');
}

function withUnit(value: unknown, unit: unknown): string | null {
  const words = recordedValue(value);
  if (words === null) return null;
  const named = text(unit);
  return named === null ? words : `${words} ${named}`;
}

function reasonOf(event: ContractEvent): string | null {
  return event.reason_code ?? text(payloadOf(event)['reason_code']);
}

function labelOf(planned: ContractEvent | undefined): string | null {
  if (planned === undefined) return null;
  const parameters = payloadOf(planned)['parameters'];
  if (!Array.isArray(parameters)) return null;
  for (const item of parameters) {
    if (typeof item === 'object' && item !== null && (item as Payload)['name'] === 'label') return text((item as Payload)['value']);
  }
  return null;
}

/** The cause of a run's end from its own events, or null while it has not ended with a reason code. */
export function stopCause(events: readonly ContractEvent[], referenceDay: string | null): StopCause | null {
  const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
  if (finished === undefined) return null;
  const end = payloadOf(finished);
  const reason = text(end['reason_code']) ?? finished.reason_code;
  if (reason === null) return null;
  const failure = typeof end['failure'] === 'object' && end['failure'] !== null ? (end['failure'] as Payload) : {};
  const observationIds = Array.isArray(failure['observation_ids']) ? failure['observation_ids'].filter((id): id is string => typeof id === 'string') : [];
  const predicates = events.filter((event) => event.kind === 'predicate.evaluated');

  const refused = [...events].reverse().find((event) => event.kind === 'action.refused' && event.action_id !== null && reasonOf(event) === reason);
  const lines: string[] = [];
  let tied = 0;
  let gripper: number | null = null;
  let measuredAt: string | null = null;
  let refusal: StopCause['refusal'] = null;
  if (refused !== undefined && refused.action_id !== null) {
    const actionId = refused.action_id;
    const before = events.filter((event) => event.action_id === actionId && event.seq < refused.seq);
    const planned = [...before].reverse().find((event) => event.kind === 'action.planned');
    const payload = payloadOf(refused);
    const actionType = text(payload['action_type']) ?? (planned === undefined ? null : text(payloadOf(planned)['action_type']));
    const label = labelOf(planned);
    const detail = text(payload['detail']);
    refusal = { action_id: actionId, seq: refused.seq, label, action_type: actionType, detail };
    const what = [label, actionType === null ? null : `(${actionType})`].filter((part) => part !== null).join(' ');
    lines.push(`Refused ${what === '' ? `action ${actionId}` : what}${detail === null ? '' : `: ${detail}`}`);
    for (const predicate of before.filter((event) => event.kind === 'predicate.evaluated' && payloadOf(event)['value'] === false)) {
      tied += 1;
      const p = payloadOf(predicate);
      const measured = withUnit(p['measured'], p['unit']);
      const threshold = withUnit(p['threshold'], p['unit']);
      const stamp = utcStamp(predicate.t_wall_utc, referenceDay);
      const values = measured === null ? 'false' : threshold === null ? measured : `${measured} against ${threshold}`;
      lines.push(`${String(p['predicate'])}: ${values}${stamp === null ? '' : `, ${stamp.text}`}`);
    }
    const measuredState = [...before].reverse().find((event) => event.kind === 'measured.state');
    if (measuredState !== undefined) {
      const m = payloadOf(measuredState);
      const velocity = baseVelocityWords(m['base_velocity']);
      if (velocity !== null) lines.push(`Base velocity in that reading: ${velocity}`);
      const joints = m['joints'];
      gripper = typeof joints === 'object' && joints !== null ? finite((joints as Payload)['gripper']) : null;
      measuredAt = measuredState.t_wall_utc;
    }
  } else {
    const measurements = Array.isArray(failure['measurements']) ? failure['measurements'] : [];
    for (const item of measurements) {
      if (typeof item !== 'object' || item === null) continue;
      const m = item as Payload;
      const value = withUnit(m['value'], m['unit']);
      if (text(m['name']) === null || value === null) continue;
      lines.push(`${String(m['name'])} ${value}`);
    }
  }
  if (lines.length === 0) lines.push(NO_STOP_MEASUREMENT);
  return {
    run_id: finished.run_id,
    reason_code: reason,
    detail: text(failure['detail']),
    refusal,
    lines,
    gripper,
    measured_at: measuredAt,
    frame_id: observationIds[0] ?? null,
    frame_ids: observationIds,
    predicates_recorded: predicates.length,
    predicates_tied: tied,
  };
}
