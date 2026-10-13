// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE CARD PER ACTIVATION (spec sections 5.5 and 6.1, audit C10). The records are the two activations of
// mission-f65c3cf8e99f on 2026-09-27, trimmed: the abort-swap REJECTED_BY_CAKE with the slot unchanged at generation 1,
// then the swapped Plan OBSERVED with the slot at generation 2 and a new capsule. 8771 serves no expectation for either
// candidate, so the refusal reads "Cake refused the successor" in the neutral, loud ink tone (verdict 2026-09-27 item
// 12), never in warn or bad and never muted; with the expectation served it reads "Refused as expected" in ok.

import { describe, expect, it } from 'vitest';
import type { MissionActivationRecord, SlotReading } from './missionRecord';
import { RUNTIME_DETAIL_LABEL, activationCard, capsuleShort } from './activationCard';

function slot(generation: string, capsule: string, instance: string, epoch: string): SlotReading {
  return { read: 'ok', slot_uuid: 's', name: 'mobile_approach', state: 'ACTIVE', capsule, generation, instance, session_uuid: '01a0e337', plan_digest: null, epoch, capability_active: null };
}

const OLD = '9600566de6c9a4cee25afaa14ff0d3e403515c181f1f345da906c73085c9389e';
const NEW = '2f34a84c19719a6ef4a040fbbd8114d831d093ef81d6a9fb78240dc8696da8a7';

const REFUSAL: MissionActivationRecord = {
  operation_uuid: '40b94b8a4bc742b89f4fe56b64e56376',
  target_id: 'cube',
  candidate_key: 'mobile-demo-active-successor-abort-swap',
  payload_digest: 'sha256:6c19',
  activation_state: 'REJECTED_BY_CAKE',
  outcome: 'REJECTED_BY_CAKE',
  transaction_phase: 'ABORTED',
  transaction_uuid: 'aa29',
  detail: 'the resident answered ABORTED; the transaction did not complete, and the answer does not say why',
  t_wall_utc: '2026-09-27T14:20:21.636Z',
  slot_before: slot('1', OLD, '6003861253238292481', '2'),
  slot_after: slot('1', OLD, '6003861253238292481', '2'),
};

const SWAP: MissionActivationRecord = {
  ...REFUSAL,
  operation_uuid: '5d293b0225f64a23b387bac05beeb2d8',
  candidate_key: 'mobile-demo-swapped',
  activation_state: 'OBSERVED',
  outcome: 'ACCEPTED',
  transaction_phase: 'COMPLETED',
  detail: 'the transaction completed',
  t_wall_utc: '2026-09-27T14:21:45.496Z',
  slot_after: slot('2', NEW, '6003579778261581826', '3'),
};

const DAY = '2026-09-27';

describe('the card of one activation', () => {
  it('reads an unexpected-free refusal as "Cake refused the successor" in the ink tone, with the slot unchanged 1 -> 1', () => {
    const card = activationCard(REFUSAL, [{ key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:6c19', role: null, label: null, expect: null }], DAY);
    expect(card.verdict).toEqual({ id: 'refused_by_cake', word: 'Cake refused the successor', tone: 'ink' });
    expect(card.headline).toBe('Cake refused the successor, slot unchanged 1 -> 1');
    expect(card.line).toBe('mobile-demo-active-successor-abort-swap, 14:20:21Z, capsule 9600566de6c9 unchanged');
    expect(card.note).toBe('This runtime serves no expected outcome for this Plan.');
    // THE RUNTIME'S WORDS IN THE RUNTIME'S VOICE (verdict 2026-09-27 item 4). The detail is the runtime's reading of the
    // resident's answer (`cake_activation.py` REJECTION_DETAIL); the resident's own answer is only the phase ABORTED.
    expect(card.answer).toBe("The runtime's detail: the resident answered ABORTED; the transaction did not complete, and the answer does not say why");
    expect(card.answer).not.toContain("The resident's answer");
    expect(['warn', 'bad']).not.toContain(card.verdict.tone);
  });

  it('adds the no-expectation note only when the candidate list was read and served this candidate without one (verifier V7)', () => {
    // The list not read, read empty, or read without this candidate: nothing here says what the runtime serves for it.
    expect(activationCard(REFUSAL, null, DAY).note).toBeNull();
    expect(activationCard(REFUSAL, [], DAY).note).toBeNull();
    expect(activationCard(REFUSAL, [{ key: 'mobile-demo-swapped', payload_digest: null, role: null, label: null, expect: null }], DAY).note).toBeNull();
    // Served, with no expectation (the field absent or null): the note is said.
    expect(activationCard(REFUSAL, [{ key: 'mobile-demo-active-successor-abort-swap', payload_digest: null }], DAY).note).toBe(
      'This runtime serves no expected outcome for this Plan.',
    );
    // The verdict does not change with the note: an unread list still never reads the refusal as a failure.
    expect(activationCard(REFUSAL, null, DAY).verdict.id).toBe('refused_by_cake');
  });

  it('reads the same refusal as "Refused as expected" in the ok tone when the candidate expects it', () => {
    const card = activationCard(REFUSAL, [{ key: 'mobile-demo-active-successor-abort-swap', payload_digest: null, role: 'refused-demo', label: 'Abort-swap', expect: 'REJECTED_BY_CAKE' }], DAY);
    expect(card.verdict).toEqual({ id: 'refused_as_expected', word: 'Refused as expected', tone: 'ok' });
    expect(card.headline).toBe('Refused as expected, slot unchanged 1 -> 1');
    expect(card.note).toBeNull();
    expect(card.label).toBe('Abort-swap');
  });

  it('reads an accepted swap as "Activated, slot 1 -> 2" with the capsule change on its second line', () => {
    const card = activationCard(SWAP, null, DAY);
    expect(card.verdict.id).toBe('activated');
    expect(card.headline).toBe('Activated, slot 1 -> 2');
    expect(card.line).toBe('mobile-demo-swapped, 14:21:45Z, capsule 9600566de6c9 -> 2f34a84c1971');
    expect(card.capsule_after_title).toBe(NEW);
    expect(card.id).toBe('5d293b0225f64a23b387bac05beeb2d8');
    expect(card.stamp?.iso).toBe('2026-09-27T14:21:45.496Z');
  });

  it('says the slot was not read both times rather than calling it unchanged', () => {
    const card = activationCard({ ...REFUSAL, slot_after: null }, [], DAY);
    expect(card.headline).toBe('Cake refused the successor; the slot was not read both times');
    expect(card.line).toBe('mobile-demo-active-successor-abort-swap, 14:20:21Z');
  });

  it('never puts a request that did not reach Cake in the resident\'s mouth: its code is the runtime\'s (verdict 2026-09-27 item 4)', () => {
    // REQUEST_FAILED: the runtime wrote the code (`cake_activation.py` submit_apply_plan); the resident never answered.
    const failed: MissionActivationRecord = { ...REFUSAL, activation_state: 'REQUEST_FAILED', outcome: 'REQUEST_FAILED', transaction_phase: null, detail: 'admin_socket_absent', slot_after: null };
    const card = activationCard(failed, null, DAY);
    expect(card.verdict.id).toBe('not_delivered');
    expect(card.answer).toBe("The runtime's detail: admin_socket_absent");
    expect(card.answer).not.toMatch(/resident/i);
    expect(RUNTIME_DETAIL_LABEL).toBe("The runtime's detail");
  });

  it('shows a digest as its first 12 hex characters and keeps the whole value for the title', () => {
    expect(capsuleShort(`sha256:${'a7'.repeat(32)}`)).toBe('a7a7a7a7a7a7');
    expect(capsuleShort(OLD)).toBe('9600566de6c9');
    expect(capsuleShort(null)).toBeNull();
  });
});
