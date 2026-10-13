// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// SYNTHETIC CAKE FIXTURES for the Cake panel's tests and for the synthetic scenarios: the four prepared roles, one check
// of each outcome the robot's resident gives them, the slots and health rows a module card is built from, and one placed
// mission's record. They are in the SHAPES the runtime serves (`GET /api/capability/check`, `robot_status` v2, a mission
// view) so a scenario drives the same parsers the live page does.
//
// NOTHING HERE IS A MEASUREMENT OF THE ROBOT. Digests, sessions and uuids are made up and say so by their repetition.
// The numbers that are the robot's own are marked where they appear: the capability evidence of the placed mission
// (27 base commands applied at generation 2, pid 43289) is the record of 2026-09-26's first far-block placement, and the
// supervisor's counters (spawns 5, restarts 5, pid 0, DEGRADED) are what the deployed robot showed with the host held.

import type { RobotHealthRow, RobotSlotRow } from '../model/robotStatus';

export const FIXTURE_SESSION = '01a0d8320c9c7b09b03c1f945f489104';
export const FIXTURE_CAPABILITY_SLOT = '505054304050806000016d6f62696c65';
export const FIXTURE_HOLDER_SLOT = '50505430405080600000686f6c646572';
export const FIXTURE_SUPERVISOR_SLOT = '50505430405080500000737570657276';
export const FIXTURE_INERT = 'c3'.repeat(32);
export const FIXTURE_MOBILE = 'b2'.repeat(32);

export const FIXTURE_KEYS = {
  activate: 'mobile-demo-swapped',
  refused: 'mobile-demo-active-successor-abort-swap',
  tampered: 'mobile-demo-swapped-tampered',
  undo: 'mobile-demo-inert',
} as const;

function reading(capsule: string, generation: string, instance: string, active: boolean): Record<string, unknown> {
  return {
    read: 'ok',
    slot_uuid: FIXTURE_CAPABILITY_SLOT,
    name: 'mobile_approach',
    state: 'ACTIVE',
    capsule,
    generation,
    instance,
    session_uuid: FIXTURE_SESSION,
    plan_digest: 'c9'.repeat(32),
    epoch: generation === '1' ? '3' : '4',
    capability_active: active,
  };
}

const HELD = { session_uuid: FIXTURE_SESSION, build_identity: 'd3'.repeat(32), host_running: false, held: true, reason_code: 'supervisor_child_not_running' };

function recorded(operation: string, key: string, extra: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_uuid: operation.repeat(16),
    target_id: null,
    candidate_key: key,
    payload_digest: 'sha256:' + operation.repeat(32),
    transaction_uuid: null,
    t_wall_utc: '2026-09-27T08:00:00.000Z',
    answer_wall_utc: '2026-09-27T08:00:01.000Z',
    requested_session_uuid: FIXTURE_SESSION,
    host_before: HELD,
    host_after: HELD,
    host_held_throughout: true,
    host_throughout_detail: null,
    resident_session_changed: false,
    unresolved: false,
    answered: true,
    orphaned: false,
    duration_ms: 900,
    ...extra,
  };
}

const TAMPERED_CHECK = recorded('a1', FIXTURE_KEYS.tampered, {
  activation_state: 'REFUSED_BEFORE_TRANSACTION',
  outcome: 'REFUSED_BEFORE_TRANSACTION',
  transaction_phase: null,
  detail: 'the resident refused the candidate before any transaction (refusal class 100, plan-unreadable): INTEGRITY: the candidate\'s bytes do not read as a Plan artifact whose digests hold, so no transaction was opened',
  slot_before: reading(FIXTURE_INERT, '1', '1', false),
  slot_after: reading(FIXTURE_INERT, '1', '1', false),
  slot_changed: false,
  refusal: { code: '100', token: 'plan-unreadable', kind: 'integrity', words: 'INTEGRITY: the candidate\'s bytes do not read as a Plan artifact whose digests hold, so no transaction was opened', rollback_class: null },
  validation: { verdict: 'invalid', plan_reason_code: '7', plan_artifact_digest: null, reason_code: null },
  state_path: { read: 'no_transaction', states: [], terminal: null, starts_at_created: null, refused_transitions: 0 },
});

const ABORTED_CHECK = recorded('b2', FIXTURE_KEYS.refused, {
  activation_state: 'REJECTED_BY_CAKE',
  outcome: 'REJECTED_BY_CAKE',
  transaction_phase: 'ABORTED',
  transaction_uuid: '57688d24e10913aa9ab0fe08f6b4720b',
  detail: 'the resident answered ABORTED; the transaction did not complete, and the answer does not say why',
  slot_before: reading(FIXTURE_INERT, '1', '1', false),
  slot_after: reading(FIXTURE_INERT, '1', '1', false),
  slot_changed: false,
  validation: { verdict: 'valid', plan_reason_code: '0', plan_artifact_digest: 'e7'.repeat(32), reason_code: null },
  state_path: { read: 'ok', states: ['CREATED', 'VALIDATED', 'PREPARED', 'QUIESCING', 'ABORTED'], terminal: 'ABORTED', starts_at_created: true, refused_transitions: 0 },
});

const OBSERVED_CHECK = recorded('c3', FIXTURE_KEYS.activate, {
  activation_state: 'OBSERVED',
  outcome: 'ACCEPTED',
  transaction_phase: 'COMPLETED',
  transaction_uuid: '22398968749b77310d8643700bfb61d3',
  detail: 'the transaction completed',
  slot_before: reading(FIXTURE_INERT, '1', '1', false),
  slot_after: reading(FIXTURE_MOBILE, '2', '2', true),
  slot_changed: true,
  validation: { verdict: 'valid', plan_reason_code: '0', plan_artifact_digest: 'e8'.repeat(32), reason_code: null },
  state_path: {
    read: 'ok',
    states: ['CREATED', 'VALIDATED', 'PREPARED', 'QUIESCING', 'ACTIVATING', 'COMMITTING', 'COMMITTED', 'DURABLE', 'RETIRING', 'COMPLETED'],
    terminal: 'COMPLETED',
    starts_at_created: true,
    refused_transitions: 0,
  },
});

const UNDO_CHECK = recorded('d4', FIXTURE_KEYS.undo, {
  activation_state: 'COMPLETED_NOT_OBSERVED',
  outcome: 'ACCEPTED',
  transaction_phase: 'COMPLETED',
  transaction_uuid: '9a1c2e3f4b5d6a7b8c9d0e1f2a3b4c5d',
  detail: 'the transaction completed but the slot does not carry the capability',
  slot_before: reading(FIXTURE_MOBILE, '2', '2', true),
  slot_after: reading(FIXTURE_INERT, '3', '3', false),
  slot_changed: true,
  validation: { verdict: 'valid', plan_reason_code: '0', plan_artifact_digest: 'e9'.repeat(32), reason_code: null },
  state_path: {
    read: 'ok',
    states: ['CREATED', 'VALIDATED', 'PREPARED', 'QUIESCING', 'ACTIVATING', 'COMMITTING', 'COMMITTED', 'DURABLE', 'RETIRING', 'COMPLETED'],
    terminal: 'COMPLETED',
    starts_at_created: true,
    refused_transitions: 0,
  },
});

/** The four prepared candidates with their roles, in the runtime's (sorted) key order. */
export const FIXTURE_CANDIDATES: readonly Record<string, unknown>[] = [
  { key: FIXTURE_KEYS.refused, payload_digest: 'sha256:' + 'b2'.repeat(32), role: 'refused-demo', label: 'Abort-swap: Cake opens the transaction and aborts it, the slot unchanged', expect: 'REJECTED_BY_CAKE' },
  { key: FIXTURE_KEYS.undo, payload_digest: 'sha256:' + 'd4'.repeat(32), role: 'undo', label: 'Undo: back to the starting Plan', expect: 'COMPLETED_NOT_OBSERVED' },
  { key: FIXTURE_KEYS.activate, payload_digest: 'sha256:' + 'c3'.repeat(32), role: 'activate', label: 'Activate the capability (the swapped Plan)', expect: 'OBSERVED' },
  { key: FIXTURE_KEYS.tampered, payload_digest: 'sha256:' + 'a1'.repeat(32), role: 'tampered-demo', label: 'Tampered twin: one byte flipped, refused by an integrity check before any transaction', expect: 'REFUSED_BEFORE_TRANSACTION' },
];

/**
 * The capability slot check as the runtime would serve it after the four beats, oldest first: the tampered twin refused
 * before any transaction, abort-swap aborted with its path, the swapped Plan observed, and the undo back to inert.
 * `checks` picks which of them are on the record (all four by default).
 */
export function fixtureCheckAnswer(checks: readonly ('tampered' | 'aborted' | 'observed' | 'undo')[] = ['tampered', 'aborted', 'observed', 'undo']): Record<string, unknown> {
  const byName = { tampered: TAMPERED_CHECK, aborted: ABORTED_CHECK, observed: OBSERVED_CHECK, undo: UNDO_CHECK };
  const newest = checks.length === 0 ? null : byName[checks[checks.length - 1] ?? 'tampered'];
  const slot = newest === null ? reading(FIXTURE_INERT, '1', '1', false) : (newest['slot_after'] as Record<string, unknown>);
  return {
    ok: true,
    capability_check_version: '1',
    available: true,
    in_flight: false,
    pending_operation_uuid: null,
    off_reason: null,
    candidates: FIXTURE_CANDIDATES.map((item) => ({ ...item })),
    current: {
      session_uuid: FIXTURE_SESSION,
      build_identity: 'd3'.repeat(32),
      host_running: false,
      host_held: true,
      host_reason_code: 'supervisor_child_not_running',
      slot,
      observed_wall_utc: '2026-09-27T08:00:02.000Z',
      age_ms: 400,
    },
    checks: checks.map((name) => ({ ...byName[name] })),
    checks_dropped: 0,
    damaged_lines: 0,
  };
}

/** The resident's slots as `robot_status` v2 lists them, with the capability ACTIVE on the swapped build. */
export const FIXTURE_SLOTS: readonly RobotSlotRow[] = [
  { name: 'mobile_approach', uuid: FIXTURE_CAPABILITY_SLOT, capsule: FIXTURE_MOBILE, generation: 2, instance: '2', state: 'ACTIVE' },
  { name: 'mobile_approach.holder', uuid: FIXTURE_HOLDER_SLOT, capsule: FIXTURE_MOBILE, generation: 1, instance: '1', state: 'ACTIVE' },
  { name: 'supervisor.lekiwi', uuid: FIXTURE_SUPERVISOR_SLOT, capsule: 'a1'.repeat(32), generation: 1, instance: '6003298303284871169', state: 'ACTIVE' },
];

/** The health rows beside them: the capability's process (pid 43289, the robot's own of 2026-09-26) and a held host. */
export const FIXTURE_HEALTH: readonly RobotHealthRow[] = [
  { slot: FIXTURE_CAPABILITY_SLOT, state_name: 'HEALTHY', child_pid: 43289, restarts: 0, spawns: 1, last_status: 0 },
  { slot: FIXTURE_SUPERVISOR_SLOT, state_name: 'DEGRADED', child_pid: 0, restarts: 5, spawns: 5, last_status: 256 },
];

/** A placed mission's record as the runtime serves it: one target carried with the capability's base commands applied. */
export const FIXTURE_PLACED_MISSION: Record<string, unknown> = {
  mission_record_version: '1',
  mission_id: 'mission-88094cda64e1',
  state: 'COMPLETE',
  data_mode: 'live',
  performs_motion: true,
  execution_label: 'proven-controller',
  activations: [],
  targets: [
    {
      target_id: 'cube',
      label: 'cube',
      state: 'PLACED',
      requires_capability: 'auto',
      runs: ['console-20260926T201540Z-mb3c1048'],
      evidence: { outcome: 'placed_new', mode: 'BASE_ADVANCE', capability: { accepted: 27, applied: 27, dropped: {}, generation: 2, pid: 43289 } },
    },
  ],
};
