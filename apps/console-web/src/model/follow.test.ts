// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AUTO-SCROLL THAT PAUSES (spec rule 6.4). Following, new entries scroll into view. The operator scrolls up: following
// pauses, and a pill counts the entries that arrived since, until the pill is pressed or the list is scrolled back to its
// end. The count changes only when entries arrive; nothing ticks.

import { describe, expect, it } from 'vitest';
import { FOLLOWING, follow, followStep, isAtEnd, unseenWords } from './follow';

describe('the timeline follow model', () => {
  it('stays following and counts nothing while entries arrive at the end', () => {
    const state = follow(FOLLOWING, { kind: 'appended', count: 3 });
    expect(state).toEqual({ following: true, unseen: 0 });
  });

  it('pauses when the operator scrolls up and counts what arrives after', () => {
    let state = follow(FOLLOWING, { kind: 'scrolled', atEnd: false });
    expect(state).toEqual({ following: false, unseen: 0 });
    state = follow(state, { kind: 'appended', count: 2 });
    state = follow(state, { kind: 'appended', count: 1 });
    expect(state).toEqual({ following: false, unseen: 3 });
  });

  it('resumes on the jump press or on a scroll back to the end', () => {
    const paused = { following: false, unseen: 3 };
    expect(follow(paused, { kind: 'jump' })).toEqual(FOLLOWING);
    expect(follow(paused, { kind: 'scrolled', atEnd: true })).toEqual(FOLLOWING);
  });

  it('does not change the count for a scroll that stays away from the end', () => {
    expect(follow({ following: false, unseen: 3 }, { kind: 'scrolled', atEnd: false })).toEqual({ following: false, unseen: 3 });
    expect(follow({ following: false, unseen: 3 }, { kind: 'appended', count: 0 })).toEqual({ following: false, unseen: 3 });
  });

  it('keeps a following list at its end when its size or layout changes, and leaves a paused one where it is', () => {
    expect(followStep(FOLLOWING, { kind: 'resized' })).toEqual({ state: FOLLOWING, scrollToEnd: true });
    const paused = { following: false, unseen: 3 };
    expect(followStep(paused, { kind: 'resized' })).toEqual({ state: { following: false, unseen: 3 }, scrollToEnd: false });
  });

  it('answers whether each event moves the list to its end', () => {
    expect(followStep(FOLLOWING, { kind: 'appended', count: 2 }).scrollToEnd).toBe(true);
    expect(followStep({ following: false, unseen: 0 }, { kind: 'appended', count: 2 }).scrollToEnd).toBe(false);
    expect(followStep(FOLLOWING, { kind: 'appended', count: 0 }).scrollToEnd).toBe(false);
    expect(followStep({ following: false, unseen: 3 }, { kind: 'jump' }).scrollToEnd).toBe(true);
    expect(followStep(FOLLOWING, { kind: 'scrolled', atEnd: false }).scrollToEnd).toBe(false);
  });

  it('calls a list at its end within 24 px', () => {
    expect(isAtEnd({ scrollTop: 476, clientHeight: 500, scrollHeight: 1000 })).toBe(true);
    expect(isAtEnd({ scrollTop: 475, clientHeight: 500, scrollHeight: 1000 })).toBe(false);
  });

  it('says the count in words', () => {
    expect(unseenWords(1)).toBe('1 new entry below');
    expect(unseenWords(3)).toBe('3 new entries below');
  });
});
