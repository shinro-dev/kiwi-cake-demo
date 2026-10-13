// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// One tick for the whole screen. What these pin is that the interval is shared rather than one per panel, that it
// stops when the last panel that wanted it has gone, and that every reader in the same tick reads the SAME number:
// two ages rendered side by side must never be measured against two different readings of the clock.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AGE_TICK_MS, nowMs, nowSubscriberCount, subscribeNow } from './clock';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('the shared age tick', () => {
  it('advances the reading on its own, which is what an age on an idle screen needs', () => {
    const stop = subscribeNow(() => undefined);
    const first = nowMs();
    vi.advanceTimersByTime(AGE_TICK_MS * 3);
    expect(nowMs() - first).toBeGreaterThanOrEqual(AGE_TICK_MS * 3);
    stop();
  });

  it('hands every reader the same reading between two ticks', () => {
    const stop = subscribeNow(() => undefined);
    const first = nowMs();
    const second = nowMs();
    expect(second).toBe(first);
    vi.advanceTimersByTime(AGE_TICK_MS);
    expect(nowMs()).toBeGreaterThan(first);
    stop();
  });

  it('runs one interval for however many panels are watching, and notifies each of them', () => {
    let left = 0;
    let right = 0;
    const stopLeft = subscribeNow(() => {
      left += 1;
    });
    const stopRight = subscribeNow(() => {
      right += 1;
    });
    expect(nowSubscriberCount()).toBe(2);
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(AGE_TICK_MS * 2);
    expect(left).toBe(2);
    expect(right).toBe(2);
    stopLeft();
    stopRight();
  });

  it('stops ticking when the last reader has gone, rather than waking the tab up for nobody', () => {
    const stop = subscribeNow(() => undefined);
    expect(vi.getTimerCount()).toBe(1);
    stop();
    expect(nowSubscriberCount()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('takes a fresh reading when nothing is ticking, so a first render is never measured against a stale one', () => {
    const before = nowMs();
    vi.setSystemTime(Date.now() + 60_000);
    expect(nowMs()).toBeGreaterThanOrEqual(before + 60_000);
  });
});
