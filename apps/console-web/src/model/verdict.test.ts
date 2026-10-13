// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE VERDICT VOCABULARY (spec section 5.1, audit C3): each record value has exactly one word, one id and one tone, on
// the main screen, in the timeline and in Diagnostics alike. Every row of the table is one case below. An unknown token
// is drawn as itself, in the mute tone.

import { describe, expect, it } from 'vitest';
import type { SlotReading } from './missionRecord';
import { activationVerdict, missionVerdict, readinessVerdict, runVerdict, signatureVerdict, slotChange } from './verdict';
import type { SignatureCheckRecord } from './signatureCheck';

const mission = (state: string, waiting_reason: string | null = null, stop_requested = false): { state: string; waiting_reason: string | null; stop_requested: boolean } => ({
  state,
  waiting_reason,
  stop_requested,
});

describe('the mission word', () => {
  it.each([
    [mission('RUNNING'), 'running', 'Running', 'accent'],
    [mission('RUNNING', null, true), 'stopping', 'Stopping', 'warn'],
    [mission('WAITING_FOR_CAPABILITY', 'capability_unavailable'), 'waiting_cake', 'Waiting for Cake', 'wait'],
    [mission('WAITING_FOR_CAPABILITY', 'operator_decision'), 'held_for_you', 'Held for you', 'warn'],
    [mission('WAITING_FOR_CAPABILITY', 'operator_away'), 'held_other', 'Held: operator_away', 'warn'],
    [mission('COMPLETE'), 'complete', 'Complete', 'ok'],
    [mission('STOPPED', null, true), 'stopped_on_request', 'Stopped on request', 'warn'],
    [mission('STOPPED'), 'stopped_no_request', 'Stopped with no stop request on its record', 'warn'],
    [mission('FAILED'), 'failed', 'Failed', 'bad'],
    [mission('PAUSED_FOR_LUNCH'), 'unknown', 'PAUSED_FOR_LUNCH', 'mute'],
  ])('%o reads %s', (record, id, word, tone) => {
    expect(missionVerdict(record)).toEqual({ id, word, tone });
  });
});

describe('the mission word for a STOPPED record (verify-r4 must_fix 1)', () => {
  // The fields of the record the runtime writes when it ends the mission it carries at its own shutdown
  // (demo_runtime/mission_coordinator.py close): nobody asked for a stop.
  const atShutdown = { state: 'STOPPED', waiting_reason: 'capability_unavailable', stop_requested: false, stop_requested_wall_utc: null, detail: 'stopped while carrying a target' };

  it('claims no request when the record says no stop was asked for', () => {
    const verdict = missionVerdict(atShutdown);
    expect(verdict).toEqual({ id: 'stopped_no_request', word: 'Stopped with no stop request on its record', tone: 'warn' });
    expect(verdict.word).not.toMatch(/on request/i);
  });

  it('claims no request when the record does not carry the field at all', () => {
    expect(missionVerdict({ state: 'STOPPED', waiting_reason: null }).id).toBe('stopped_no_request');
  });

  it('says "Stopped on request" only when the record carries the request', () => {
    const asked = { ...atShutdown, stop_requested: true, stop_requested_wall_utc: '2026-09-28T10:11:00.000Z' };
    expect(missionVerdict(asked)).toEqual({ id: 'stopped_on_request', word: 'Stopped on request', tone: 'warn' });
  });
});

describe('the run word', () => {
  const run = (outcome: string | null, extra: Partial<Parameters<typeof runVerdict>[0]> = {}): Parameters<typeof runVerdict>[0] => ({
    outcome,
    reason_code: null,
    stop_requested: false,
    placement: true,
    non_placement_done: false,
    ...extra,
  });
  it.each([
    [run('placed_new'), 'placed', 'Placed', 'ok'],
    [run('already_placed_noop'), 'already_placed', 'Already placed', 'ok'],
    [run('stopped', { stop_requested: true }), 'stopped_on_request', 'Stopped on request', 'warn'],
    [run('stopped', { reason_code: 'base_not_zero_before_segment' }), 'stopped_by_controller', 'Stopped by the controller', 'bad'],
    [run('refused'), 'refused', 'Refused', 'bad'],
    [run('failed', { reason_code: 'mission_capability_required' }), 'needs_capability', 'Needs capability', 'wait'],
    [run('failed', { reason_code: 'mission_fallback_capability_required' }), 'needs_capability', 'Needs capability', 'wait'],
    [run('failed', { reason_code: 'align_cube_not_detected' }), 'failed', 'Failed', 'bad'],
    [run('unknown'), 'no_outcome', 'No stated outcome', 'warn'],
    [run('unknown', { placement: false, non_placement_done: true }), 'done', 'Done', 'mute'],
    [run('exploded'), 'unknown', 'exploded', 'mute'],
  ])('%o reads %s', (input, id, word, tone) => {
    expect(runVerdict(input)).toEqual({ id, word, tone });
  });
});

function slot(generation: string, capsule: string, instance = generation, read = 'ok'): SlotReading {
  return {
    read,
    slot_uuid: 'slot',
    name: 'mobile_approach',
    state: 'ACTIVE',
    capsule,
    generation,
    instance,
    session_uuid: 's',
    plan_digest: null,
    epoch: '2',
    capability_active: null,
  };
}

const SAME = { slot_before: slot('1', '9600566de6c9'), slot_after: slot('1', '9600566de6c9') };
const MOVED = { slot_before: slot('1', '9600566de6c9'), slot_after: slot('2', '2f34a84c1971') };

describe('the activation word', () => {
  it.each([
    ['OBSERVED', 'OBSERVED', MOVED, 'activated', 'Activated', 'ok'],
    // NO EXPECTATION SERVED, NO GREEN (should_fix honesty S4): the live update is a fact the neutral bright ink says loud,
    // as the refusal with no expectation is; green is for an outcome the runtime's configuration said to expect.
    ['OBSERVED', null, MOVED, 'activated', 'Activated', 'ink'],
    ['OBSERVED', 'REJECTED_BY_CAKE', MOVED, 'activated_unexpected', 'Activated, not as expected', 'bad'],
    ['REJECTED_BY_CAKE', 'REJECTED_BY_CAKE', SAME, 'refused_as_expected', 'Refused as expected', 'ok'],
    ['REJECTED_BY_CAKE', null, SAME, 'refused_by_cake', 'Cake refused the successor', 'ink'],
    ['REFUSED_BEFORE_TRANSACTION', 'REFUSED_BEFORE_TRANSACTION', SAME, 'refused_before_as_expected', 'Refused before any transaction, as expected', 'ok'],
    ['REFUSED_BEFORE_TRANSACTION', null, SAME, 'refused_before', 'Cake refused the successor before any transaction', 'ink'],
    ['REJECTED_BY_CAKE', 'OBSERVED', SAME, 'refused_unexpected', 'Refused, not as expected', 'bad'],
    ['REFUSED_BEFORE_TRANSACTION', 'OBSERVED', SAME, 'refused_unexpected', 'Refused, not as expected', 'bad'],
    ['REJECTED_BY_CAKE', 'REJECTED_BY_CAKE', MOVED, 'refused_slot_changed', 'Refused, but the slot changed', 'bad'],
    ['REJECTED_BY_CAKE', null, MOVED, 'refused_slot_changed', 'Refused, but the slot changed', 'bad'],
    ['COMPLETED_NOT_OBSERVED', 'COMPLETED_NOT_OBSERVED', MOVED, 'inert_as_expected', 'Back to inert, as expected', 'ok'],
    ['COMPLETED_NOT_OBSERVED', null, MOVED, 'completed_inactive', 'Completed, capability not active', 'warn'],
    ['REQUEST_FAILED', null, SAME, 'not_delivered', 'Did not reach Cake', 'warn'],
    ['SUBMITTED', null, SAME, 'pending', "Waiting for Cake's answer", 'wait'],
    ['SWALLOWED', null, SAME, 'unknown', 'SWALLOWED', 'mute'],
  ])('%s with expect %s reads %s', (state, expectState, slots, id, word, tone) => {
    expect(activationVerdict({ activation_state: state, ...slots }, expectState)).toEqual({ id, word, tone });
  });

  it('never says a refusal was as expected when the slot reads do not show it unchanged', () => {
    const unread = { slot_before: slot('1', 'a', '1', 'failed'), slot_after: slot('1', 'a') };
    expect(activationVerdict({ activation_state: 'REJECTED_BY_CAKE', ...unread }, 'REJECTED_BY_CAKE').id).toBe('refused_by_cake');
    expect(activationVerdict({ activation_state: 'REJECTED_BY_CAKE', slot_before: null, slot_after: null }, 'REJECTED_BY_CAKE').id).toBe('refused_by_cake');
    // The refusal before any transaction too (should_fix honesty S4): green only with both reads showing the slot unchanged.
    expect(activationVerdict({ activation_state: 'REFUSED_BEFORE_TRANSACTION', ...unread }, 'REFUSED_BEFORE_TRANSACTION')).toEqual({
      id: 'refused_before',
      word: 'Cake refused the successor before any transaction',
      tone: 'ink',
    });
    expect(activationVerdict({ activation_state: 'REFUSED_BEFORE_TRANSACTION', slot_before: null, slot_after: null }, 'REFUSED_BEFORE_TRANSACTION').tone).toBe('ink');
  });

  it('is green only with a served expectation that the record met, and for a refusal only with the slot read unchanged both times', () => {
    const unread = { slot_before: slot('1', 'a', '1', 'failed'), slot_after: slot('1', 'a') };
    const states = ['OBSERVED', 'REJECTED_BY_CAKE', 'REFUSED_BEFORE_TRANSACTION', 'COMPLETED_NOT_OBSERVED', 'REQUEST_FAILED', 'SUBMITTED', 'SWALLOWED'];
    const greens: string[] = [];
    for (const state of states) {
      for (const expectState of [null, ...states]) {
        for (const [label, slots] of [['same', SAME], ['moved', MOVED], ['unread', unread], ['none', { slot_before: null, slot_after: null }]] as const) {
          const verdict = activationVerdict({ activation_state: state, ...slots }, expectState);
          if (verdict.tone !== 'ok') continue;
          greens.push(`${state}/${String(expectState)}/${label}`);
          expect(expectState).toBe(state);
          if (state === 'REJECTED_BY_CAKE' || state === 'REFUSED_BEFORE_TRANSACTION') expect(label).toBe('same');
        }
      }
    }
    expect(greens).toContain('REFUSED_BEFORE_TRANSACTION/REFUSED_BEFORE_TRANSACTION/same');
    expect(greens).toContain('OBSERVED/OBSERVED/moved');
  });
});

describe('the slot change', () => {
  it('is unchanged only when both reads answered and generation, capsule and instance are equal', () => {
    expect(slotChange(SAME.slot_before, SAME.slot_after)).toBe('unchanged');
    expect(slotChange(MOVED.slot_before, MOVED.slot_after)).toBe('changed');
    expect(slotChange(slot('1', 'a', '5'), slot('1', 'a', '6'))).toBe('changed');
    expect(slotChange(slot('1', 'a', '1', 'failed'), slot('1', 'a'))).toBe('unknown');
    expect(slotChange(null, slot('1', 'a'))).toBe('unknown');
  });
});

function check(extra: Partial<SignatureCheckRecord>): SignatureCheckRecord {
  return {
    check_uuid: 'c1',
    runtime_session: 'rt-now',
    t_wall_utc: '2026-09-27T14:00:00.000Z',
    policy: 'require',
    trusted_keys: [],
    throwaway_public_key: null,
    capsules: 3,
    unsigned_twins: 0,
    rows: [],
    rows_as_expected: 6,
    rows_not_as_expected: 0,
    rows_skipped: 0,
    rows_error: 0,
    as_expected: true,
    complete: true,
    duration_ms: 49,
    ...extra,
  };
}

describe('the signature check word', () => {
  it.each([
    [check({}), 'sig_ok', 'Signatures as expected', 'ok'],
    [check({ rows_skipped: 1, complete: false }), 'sig_partial', 'As expected, 1 skipped', 'warn'],
    [check({ rows_not_as_expected: 1, as_expected: false }), 'sig_bad', 'Not as expected', 'bad'],
    [check({ rows_error: 1, complete: false }), 'sig_bad', 'Not as expected', 'bad'],
    [check({ runtime_session: 'rt-20260926T184836Z-e34dcd31', rows_skipped: 1 }), 'sig_earlier', 'Earlier session', 'mute'],
    [null, 'sig_none', 'No signature check in this session', 'mute'],
  ])('%o reads %s', (record, id, word, tone) => {
    expect(signatureVerdict(record, 'rt-now')).toEqual({ id, word, tone });
  });
});

describe('the readiness word', () => {
  it.each([
    ['ready', 'Ready', 'ok'],
    ['not_ready', 'Not ready', 'bad'],
    ['steps_first', 'Steps first', 'warn'],
    ['running', 'Running', 'accent'],
    ['waiting_cake', 'Waiting for Cake', 'wait'],
    ['held_for_you', 'Held for you', 'warn'],
  ] as const)('%s reads %s', (id, word, tone) => {
    expect(readinessVerdict(id)).toEqual({ id, word, tone });
  });
});
