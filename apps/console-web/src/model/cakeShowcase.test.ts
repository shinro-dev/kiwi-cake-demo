// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAKE SHOWCASE, ON THE MODEL: candidate roles and their expected outcomes, nothing preselected, a refusal before
// any transaction named as Cake judging (an integrity refusal, never a signature one), ValidatePlan's verdict and the
// transaction's state path read as the runtime recorded them, and participation read from the mission record.

import { describe, expect, it } from 'vitest';
import { activationLine, missionCandidateChoice } from '../design/cake';
import {
  checkChoice,
  checkExpectation,
  checkHeadline,
  parseCapabilityCheckState,
  primaryCandidate,
  statePathWords,
  validationWords,
} from './capabilityCheck';
import type { CapabilityCheckState } from './capabilityCheck';
import { CAKE_TASK_SENTENCE, cakeTaskSentence, participationFromMission } from './cakeTask';
import type { MissionRecord } from './missionRecord';

const SESSION = '01a0d8320c9c7b09b03c1f945f489104';

function answer(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    capability_check_version: '1',
    in_flight: false,
    pending_operation_uuid: null,
    off_reason: null,
    candidates: [
      { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:' + 'cd'.repeat(32), role: 'refused-demo', label: 'Abort-swap', expect: 'REJECTED_BY_CAKE' },
      { key: 'mobile-demo-inert', payload_digest: 'sha256:' + 'ee'.repeat(32), role: 'undo', label: 'Undo', expect: 'COMPLETED_NOT_OBSERVED' },
      { key: 'mobile-demo-swapped', payload_digest: 'sha256:' + 'ab'.repeat(32), role: 'activate', label: 'Activate', expect: 'OBSERVED' },
      { key: 'mobile-demo-swapped-tampered', payload_digest: 'sha256:' + 'ff'.repeat(32), role: 'tampered-demo', label: 'Tampered twin', expect: 'REFUSED_BEFORE_TRANSACTION' },
    ],
    current: { session_uuid: SESSION, host_held: true, host_running: false, age_ms: 10 },
    checks: [],
    checks_dropped: 0,
    ...extra,
  };
}

function parsed(extra: Record<string, unknown> = {}): CapabilityCheckState {
  const state = parseCapabilityCheckState(answer(extra));
  if (state === null) throw new Error('not parsed');
  return state;
}

function check(extra: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_uuid: 'ab'.repeat(16),
    candidate_key: 'mobile-demo-swapped-tampered',
    activation_state: 'REFUSED_BEFORE_TRANSACTION',
    outcome: 'REFUSED_BEFORE_TRANSACTION',
    transaction_phase: null,
    detail: 'the resident refused the candidate before any transaction (refusal class 100, plan-unreadable)',
    answered: true,
    slot_changed: false,
    refusal: { code: '100', token: 'plan-unreadable', kind: 'integrity', words: 'INTEGRITY: the bytes do not hold', rollback_class: null },
    validation: { verdict: 'invalid', plan_reason_code: '7', plan_artifact_digest: null },
    state_path: { read: 'no_transaction', states: [], terminal: null, starts_at_created: null },
    ...extra,
  };
}

describe('candidate roles', () => {
  it('reads each role, its label and the outcome it expects, in the order the runtime sends them', () => {
    const state = parsed();
    expect(state.roles.map((item) => item.role)).toEqual(['refused-demo', 'undo', 'activate', 'tampered-demo']);
    expect(state.roles.find((item) => item.role === 'tampered-demo')?.expect).toBe('REFUSED_BEFORE_TRANSACTION');
  });

  it('never preselects: with keys [abort-swap, swapped] and no choice, nothing is chosen', () => {
    const state = parsed({ candidates: answer().candidates });
    expect(checkChoice(state, null)).toBeNull();
    expect(checkChoice(state, 'gone')).toBeNull();
    expect(checkChoice(state, 'mobile-demo-swapped')).toBe('mobile-demo-swapped');
    const bare = parsed({
      candidates: [
        { key: 'mobile-demo-active-successor-abort-swap', payload_digest: 'sha256:' + 'cd'.repeat(32) },
        { key: 'mobile-demo-swapped', payload_digest: 'sha256:' + 'ab'.repeat(32) },
      ],
    });
    expect(checkChoice(bare, null)).toBeNull();
  });

  it('a waiting mission s candidate is never preselected either', () => {
    const offered = [{ key: 'mobile-demo-active-successor-abort-swap' }, { key: 'mobile-demo-swapped' }];
    expect(missionCandidateChoice(offered, null)).toBeNull();
    expect(missionCandidateChoice(offered, 'gone')).toBeNull();
    expect(missionCandidateChoice(offered, 'mobile-demo-swapped')).toBe('mobile-demo-swapped');
    expect(missionCandidateChoice(null, 'mobile-demo-swapped')).toBeNull();
  });

  it('the primary action is the activate role and nothing else', () => {
    expect(primaryCandidate(parsed())).toBe('mobile-demo-swapped');
    const bare = parsed({ candidates: [{ key: 'a', payload_digest: 'sha256:' + 'ab'.repeat(32) }] });
    expect(primaryCandidate(bare)).toBeNull();
    expect(bare.roles[0]?.role).toBeNull();
    expect(bare.roles[0]?.label).toBe('a');
  });

  it('a role this console does not name is read as no role', () => {
    const odd = parsed({ candidates: [{ key: 'a', payload_digest: 'sha256:' + 'ab'.repeat(32), role: 'deploy', expect: 'OBSERVED' }] });
    expect(odd.roles[0]?.role).toBeNull();
  });
});

describe('a refusal before any transaction', () => {
  it('is Cake judging, headed as a refusal and never as a request that did not land', () => {
    // THE SLOT AS THE RUNTIME READ IT ON BOTH SIDES (demo_runtime/capability_check.py records slot_before and slot_after on
    // every check). Green needs them (should_fix honesty S4): this case carried none and was green on the expectation alone.
    const read = { read: 'ok', slot_uuid: 'slot', name: 'mobile_approach', state: 'ACTIVE', capsule: '9600566de6c9', generation: '1', instance: '1', session_uuid: SESSION, plan_digest: null, epoch: '2', capability_active: true };
    const state = parsed({ checks: [check({ slot_before: read, slot_after: read })] });
    const recorded = state.checks[0];
    if (recorded === undefined) throw new Error('no check');
    // Spec 5.1: the tampered role expects this refusal, so it reads as expected in the ok tone; with no role served it
    // is the neutral bright ink (verdict 2026-09-27 item 12). Never red for being a refusal (verifier r2 defect 3).
    const headline = checkHeadline(recorded, state.roles);
    expect(headline.tone).toBe('ok');
    expect(headline.words).toContain('as expected');
    expect(checkHeadline(recorded, []).tone).toBe('ink');
    // With the slot not read, the expectation alone is no record: the neutral refusal, never green.
    const unread = parsed({ checks: [check({})] }).checks[0];
    if (unread === undefined) throw new Error('no check');
    expect(checkHeadline(unread, state.roles).tone).toBe('ink');
    expect(checkHeadline(unread, state.roles).words).not.toContain('as expected');
    expect(headline.words).toContain('before any transaction');
    expect(headline.words.toLowerCase()).toContain('integrity');
    expect(headline.words.toLowerCase()).not.toContain('signature');
    expect(headline.words).not.toContain('did not land');
    expect(recorded.refusal?.token).toBe('plan-unreadable');
  });

  it('meets the tampered role s expectation', () => {
    const state = parsed({ checks: [check({})] });
    const recorded = state.checks[0];
    if (recorded === undefined) throw new Error('no check');
    expect(checkExpectation(recorded, state.roles)).toEqual({ met: true, words: expect.stringContaining('as expected') });
    const surprise = parsed({ checks: [check({ activation_state: 'OBSERVED' })] }).checks[0];
    if (surprise === undefined) throw new Error('no check');
    expect(checkExpectation(surprise, state.roles).met).toBe(false);
  });

  it('is its own verdict on a mission activation too', () => {
    const line = activationLine({
      operation_uuid: 'ab'.repeat(16),
      target_id: null,
      candidate_key: 'x',
      payload_digest: null,
      activation_state: 'REFUSED_BEFORE_TRANSACTION',
      outcome: 'REFUSED_BEFORE_TRANSACTION',
      transaction_phase: null,
      transaction_uuid: null,
      detail: null,
      t_wall_utc: null,
    });
    expect(line.verdict).toBe('refused_before_transaction');
    expect(line.activated).toBe(false);
    expect(line.sentence).toContain('no transaction');
    expect(line.sentence.toLowerCase()).not.toContain('signature');
  });
});

describe('ValidatePlan and the state path', () => {
  it('says the verdict of the exact bytes, and says when it was not asked', () => {
    const state = parsed({ checks: [check({}), check({ operation_uuid: 'cd'.repeat(16), validation: null }), check({ operation_uuid: 'ef'.repeat(16), validation: { verdict: 'valid', plan_reason_code: '0', plan_artifact_digest: 'ab'.repeat(32) } })] });
    const [invalid, missing, valid] = state.checks;
    if (invalid === undefined || missing === undefined || valid === undefined) throw new Error('checks');
    expect(validationWords(invalid)).toContain('invalid');
    expect(validationWords(invalid)).toContain('7');
    expect(validationWords(missing)).toContain('not asked');
    expect(validationWords(valid)).toContain('valid');
  });

  it('prints the path in the order the resident took it, and never draws a path that is not there', () => {
    const state = parsed({
      checks: [
        check({ state_path: { read: 'ok', states: ['CREATED', 'VALIDATED', 'PREPARED', 'ABORTED'], terminal: 'ABORTED', starts_at_created: true } }),
        check({ operation_uuid: 'cd'.repeat(16) }),
        check({ operation_uuid: 'ef'.repeat(16), state_path: { read: 'ok', states: ['PREPARED', 'COMPLETED'], terminal: 'COMPLETED', starts_at_created: false } }),
        check({ operation_uuid: '12'.repeat(16), state_path: { read: 'not_in_ring', states: [], terminal: null } }),
      ],
    });
    const [aborted, refused, lost, gone] = state.checks;
    if (aborted === undefined || refused === undefined || lost === undefined || gone === undefined) throw new Error('checks');
    expect(statePathWords(aborted)).toBe('CREATED > VALIDATED > PREPARED > ABORTED');
    expect(statePathWords(refused)).toContain('no transaction was opened');
    expect(statePathWords(lost)).toContain('the ring no longer holds its start');
    expect(statePathWords(gone)).toContain('no longer holds');
  });
});

describe('participation, read from the mission record', () => {
  const placed = {
    mission_id: 'mission-88094cda64e1',
    state: 'COMPLETE',
    activations: [],
    targets: [{ target_id: 'cube', state: 'PLACED', evidence: { mode: 'BASE_ADVANCE', capability: { accepted: 27, applied: 27, dropped: {}, generation: 2, pid: 43289 } } }],
  };

  it('both placed missions of the day took part, with the module, its generation and its pid named', () => {
    for (const applied of [27, 20]) {
      const raw = { ...placed, targets: [{ ...placed.targets[0], evidence: { capability: { accepted: applied, applied, generation: 2, pid: 43289 } } }] };
      const participation = participationFromMission(raw, 'mobile_approach');
      expect(participation.value).toBe(true);
      expect(participation.evidence).toContain('mobile_approach');
      expect(participation.evidence).toContain('generation 2');
      expect(participation.evidence).toContain('pid 43289');
      expect(participation.evidence).toContain(`${String(applied)} base commands`);
    }
  });

  it('nothing applied, no pid or no mission is not participation', () => {
    expect(participationFromMission({ ...placed, targets: [{ target_id: 'cube', evidence: { capability: { applied: 0, generation: 2, pid: 1 } } }] }, null).value).toBe(false);
    expect(participationFromMission({ ...placed, targets: [{ target_id: 'cube', evidence: { capability: { applied: 3, generation: 2 } } }] }, null).value).toBe(false);
    expect(participationFromMission(null, null).value).toBe(false);
  });

  it('the task sentence names the module when the record says it took part, and keeps the constant when it did not', () => {
    const mission = { mission_id: 'm', state: 'COMPLETE', activations: [], targets: [] } as unknown as MissionRecord;
    const participation = participationFromMission(placed, 'mobile_approach');
    const sentence = cakeTaskSentence(mission, participation);
    expect(sentence).not.toBe(CAKE_TASK_SENTENCE);
    expect(sentence).toContain('mobile_approach');
    expect(sentence).not.toContain('not connected');
    expect(cakeTaskSentence(null)).toBe(CAKE_TASK_SENTENCE);
    expect(cakeTaskSentence(mission)).toBe(CAKE_TASK_SENTENCE);
  });
});
