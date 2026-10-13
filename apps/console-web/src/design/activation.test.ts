// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE THREE THINGS AN ACTIVATION RESULT MUST NEVER BE CONFLATED INTO.
//
// Every one of these is a sentence somebody would write without thinking about it, and every one of them sends a
// reader somewhere false:
//
//   calling a request that never landed a rejection, which states a verdict on a candidate nobody read;
//   explaining an aborted transaction as a failed upload, a bad signature or a corrupted binary, none of which is in
//     the record and each of which sends somebody looking for a fault that is not there;
//   reading a transaction that reports itself complete as a capability that is now present, when the only evidence of
//     that is the slot read back afterwards.
//
// The wording lives in one function so that it can be pinned here, once, instead of being written four times on a
// screen and drifting.

import { describe, expect, it } from 'vitest';
import { activationLine } from './cake';
import type { MissionActivationRecord } from '../model/missionRecord';

function activation(extra: Partial<MissionActivationRecord> = {}): MissionActivationRecord {
  return {
    operation_uuid: 'op-0001',
    target_id: 'cube-b',
    candidate_key: 'candidate-0001',
    payload_digest: 'sha256:' + 'ab'.repeat(32),
    activation_state: 'SUBMITTED',
    outcome: null,
    transaction_phase: null,
    transaction_uuid: null,
    detail: null,
    t_wall_utc: '2026-09-23T10:05:00.000Z',
    ...extra,
  };
}

/** Every word of one line, for the prohibitions. */
function words(record: MissionActivationRecord): string {
  const line = activationLine(record);
  return `${line.headline} ${line.sentence} ${line.qualifier ?? ''}`.toLowerCase();
}

describe('a request that did not land', () => {
  const failed = activation({ activation_state: 'REQUEST_FAILED', detail: 'the socket was not accepting.' });

  it('is never worded as Cake rejecting, refusing or judging anything', () => {
    const said = words(failed);
    for (const forbidden of ['reject', 'refus', 'denied', 'judged the', 'invalid candidate']) {
      expect(said).not.toContain(forbidden);
    }
    expect(said).toContain('did not reach the resident');
    expect(said).toContain('nothing was judged');
  });

  it('is not an activation', () => {
    expect(activationLine(failed).activated).toBe(false);
    expect(activationLine(failed).verdict).toBe('not_delivered');
  });
});

describe('a transaction Cake aborted', () => {
  const rejected = activation({ activation_state: 'REJECTED_BY_CAKE' });

  // CHANGED DELIBERATELY. This used to pin "not a capsule this resident admitted" as the honest cause. The handoff
  // measured the same admitted candidate completing on a fresh resident and aborting once the resident was past its
  // transaction deadline (docs/autonomous-demo/cake-console-handoff-2026-09-23.md, section 8), so that cause can be
  // false, and the resident's answer carries no cause at all. What is pinned now is the abort of the ApplyPlan operation
  // and the absence of a stated cause.
  it('is stated as an abort of the ApplyPlan operation, with no cause the answer does not carry', () => {
    const said = words(rejected);
    expect(said).toContain('aborted');
    expect(said).toContain('applyplan transaction that carried the prepared candidate');
    expect(said).toContain('not why');
    expect(said).not.toContain('not a capsule this resident admitted');
  });

  // The detail here is the one the runtime now writes for an abort (demo_runtime/cake_activation.py REJECTION_DETAIL).
  // It was the old "not a capsule this resident admitted", which the runtime no longer says because it can be false.
  it("prints the runtime's detail as the runtime's, once, and never as this console's", () => {
    const detail = 'the resident answered ABORTED; the transaction did not complete, and the answer does not say why';
    const sentence = activationLine(activation({ activation_state: 'REJECTED_BY_CAKE', detail })).sentence;
    expect(sentence).toContain(`The runtime's detail: ${detail}.`);
    expect(sentence.split(detail)).toHaveLength(2);
  });

  it('never blames an upload, a signature or a corrupted binary', () => {
    const said = words(rejected);
    for (const forbidden of ['upload', 'signature', 'signing', 'corrupt', 'checksum', 'truncated', 'transfer']) {
      expect(said).not.toContain(forbidden);
    }
  });

  it('is not an activation', () => {
    expect(activationLine(rejected).activated).toBe(false);
    expect(activationLine(rejected).verdict).toBe('aborted');
  });
});

describe('a submitted activation', () => {
  it('stays pending, and says so, until the slot is read back', () => {
    const line = activationLine(activation({ activation_state: 'SUBMITTED' }));
    expect(line.verdict).toBe('pending');
    expect(line.activated).toBe(false);
    expect(line.headline.toLowerCase()).toContain('not yet activated');
  });

  it('is STILL pending when the transaction reports itself completed', () => {
    // The one that matters. A completed transaction is not a capability: the read-back is the evidence.
    const line = activationLine(activation({ activation_state: 'SUBMITTED', transaction_phase: 'COMPLETED', outcome: 'ok' }));
    expect(line.activated).toBe(false);
    expect(line.verdict).toBe('pending');
    expect(line.qualifier).not.toBe(null);
    expect((line.qualifier ?? '').toLowerCase()).toContain('a phase is not an activation');
    expect(line.qualifier).toContain('COMPLETED');
  });
});

describe('the slot read back', () => {
  it('is the only state drawn as activated', () => {
    const observed = activationLine(activation({ activation_state: 'OBSERVED', transaction_phase: 'COMPLETED' }));
    expect(observed.activated).toBe(true);
    expect(observed.verdict).toBe('activated');
    expect(observed.sentence.toLowerCase()).toContain('read back');
    // Nothing to qualify: this one IS the read-back.
    expect(observed.qualifier).toBe(null);

    for (const state of ['SUBMITTED', 'REJECTED_BY_CAKE', 'REQUEST_FAILED', 'SOMETHING_NEWER']) {
      expect(activationLine(activation({ activation_state: state })).activated).toBe(false);
    }
  });
});

describe('a state this console has never been told about', () => {
  it('is printed as the runtime wrote it and is not read as an activation', () => {
    const line = activationLine(activation({ activation_state: 'PARTIALLY_SOMETHING' }));
    expect(line.headline).toBe('PARTIALLY_SOMETHING');
    expect(line.verdict).toBe('unknown');
    expect(line.activated).toBe(false);
  });
});

describe('the runtime own words', () => {
  it('are carried through rather than replaced', () => {
    const line = activationLine(activation({ activation_state: 'REQUEST_FAILED', detail: 'connect refused by the admin socket.' }));
    expect(line.sentence).toContain('connect refused by the admin socket.');
  });
});
