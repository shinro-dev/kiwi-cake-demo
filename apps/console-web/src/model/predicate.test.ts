// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A verdict and the freshness of what it rests on are two different facts, and the second one is the one a console
// can get wrong without anybody noticing. These tests pin that a missing value never becomes a false, that a record
// which does not say which state it is in is not made to say it, and that no age is ever produced by subtracting two
// clocks that were not read on the same machine in the same session.

import { describe, expect, it } from 'vitest';
import type { SourceStamp } from '@kiwi/demo-contracts';
import { decisionAgeNs, predicateState, predicateStateWasRecorded } from './predicate';
import type { PredicateView } from './types';

function stamp(extra: Partial<SourceStamp> = {}): SourceStamp {
  return { session: 'host-session-a', seq: 100, sample_mono_ns: 5_000_000_000, clock_domain: 'pi_monotonic', ...extra };
}

function predicate(extra: Partial<PredicateView> = {}): PredicateView {
  return {
    predicate: 'clearance_target_met',
    value: true,
    state: null,
    reason_code: null,
    rule_ref: null,
    rule_digest: null,
    threshold: 3,
    measured: 30,
    unit: 'mm',
    evidence: [],
    consumed_source_stamp: stamp(),
    measurements: [],
    seq: 12,
    t_mono_ns: 12_000_000,
    clock_domain: 'laptop_monotonic',
    ...extra,
  };
}

describe('which state a recorded verdict is in', () => {
  it('takes the state the record states, and says that the record stated it', () => {
    const view = predicate({ value: null, state: 'unavailable', reason_code: 'no_frame_was_kept' });
    expect(predicateState(view)).toBe('unavailable');
    expect(predicateStateWasRecorded(view)).toBe(true);
  });

  it('reads a record written before the field existed the way it has always been read', () => {
    expect(predicateState(predicate({ value: true, state: null }))).toBe('evaluated');
    expect(predicateState(predicate({ value: false, state: null }))).toBe('evaluated');
    expect(predicateState(predicate({ value: null, state: null }))).toBe('not_evaluated');
    expect(predicateStateWasRecorded(predicate({ state: null }))).toBe(false);
  });

  it('never turns a predicate that was not evaluated into one that measured false', () => {
    const view = predicate({ value: null, state: null });
    expect(predicateState(view)).not.toBe('evaluated');
    expect(view.value).toBeNull();
  });
});

describe('how old the evidence was when the verdict was taken', () => {
  it('is measured inside ONE clock of ONE session of the source', () => {
    const answer = decisionAgeNs(predicate(), stamp({ seq: 140, sample_mono_ns: 5_250_000_000 }));
    expect(answer.age_ns).toBe(250_000_000);
    expect(answer.refusal).toBeNull();
  });

  it('refuses to measure across a session change of the source, which is a restart', () => {
    const answer = decisionAgeNs(predicate(), stamp({ session: 'host-session-b', sample_mono_ns: 1_000 }));
    expect(answer.age_ns).toBeNull();
    expect(answer.refusal).toBe('the newest sample is from another session of that source');
  });

  it('refuses to measure across two different clock domains', () => {
    const answer = decisionAgeNs(predicate(), stamp({ clock_domain: 'laptop_monotonic', sample_mono_ns: 9_000_000_000 }));
    expect(answer.age_ns).toBeNull();
    expect(answer.refusal).toBe('the newest sample is read in another clock domain');
  });

  it('says so when the verdict names no sample at all, rather than reporting a zero', () => {
    const answer = decisionAgeNs(predicate({ consumed_source_stamp: null }), stamp());
    expect(answer.age_ns).toBeNull();
    expect(answer.refusal).toBe('the verdict names no source sample');
  });

  it('says so when nothing has arrived from that source since the verdict', () => {
    expect(decisionAgeNs(predicate(), null).refusal).toBe('no sample of that source has arrived since');
  });

  it('says so when a stamp carries no source time to subtract', () => {
    const answer = decisionAgeNs(predicate({ consumed_source_stamp: stamp({ sample_mono_ns: null }) }), stamp());
    expect(answer.age_ns).toBeNull();
    expect(answer.refusal).toBe('neither sample carries a source time');
  });

  it('never returns a negative age when a sample older than the verdict is the newest one held', () => {
    expect(decisionAgeNs(predicate(), stamp({ sample_mono_ns: 4_000_000_000 })).age_ns).toBe(0);
  });
});
