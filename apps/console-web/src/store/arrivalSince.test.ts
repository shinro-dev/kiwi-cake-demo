// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE STORE KEEPS WHEN EACH FEED'S CURRENT RUN OF ARRIVALS BEGAN (browser r2 N4, verifier R-2): the tap, the stream and
// the snapshot poll. Diagnostics says those times, which move only when a feed goes silent or fails and comes back,
// and never the newest sample's time, which moved every second at a quiet end state.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { TelemetryMessage } from '../api/types';
import { ConsoleStore } from './store';

const T0 = Date.parse('2026-09-27T14:24:00.000Z');

beforeEach(() => {
  vi.useFakeTimers({ now: T0 });
});

afterEach(() => {
  vi.useRealTimers();
});

/** A store over the synthetic source whose telemetry this test pushes, and whose next snapshot read can be failed. */
async function started(): Promise<{ store: ConsoleStore; push: (message: TelemetryMessage) => void; failNextSnapshot: () => void }> {
  const base = new SyntheticSource();
  let push: (message: TelemetryMessage) => void = () => undefined;
  let fail = false;
  const source = new Proxy(base, {
    get(target, key) {
      if (key === 'subscribeTelemetry') {
        return (onMessage: (message: TelemetryMessage) => void) => {
          push = onMessage;
          return () => undefined;
        };
      }
      if (key === 'snapshot') {
        return async () => {
          if (fail) {
            fail = false;
            throw new Error('the runtime did not answer');
          }
          return target.snapshot();
        };
      }
      const value: unknown = Reflect.get(target, key, target);
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  });
  const store = new ConsoleStore(source, { storage: null });
  await store.start();
  return { store, push: (message) => push(message), failNextSnapshot: () => (fail = true) };
}

const TAP = { tap: { state: 'connected', reason: null, available: true, measured_rate_hz: 10 } } as unknown as TelemetryMessage;

describe('when each arriving feed began', () => {
  it('keeps the tap and the stream at the first sample while samples keep arriving, and restarts after a silence', async () => {
    const { store, push } = await started();
    push(TAP);
    await vi.advanceTimersByTimeAsync(200);
    const first = store.getSnapshot().telemetry;
    expect(first.tap_since_ms).toBe(T0);
    expect(first.line_since_ms).toBe(T0);
    for (let second = 1; second <= 6; second += 1) {
      await vi.advanceTimersByTimeAsync(800);
      push(TAP);
    }
    await vi.advanceTimersByTimeAsync(200);
    const later = store.getSnapshot().telemetry;
    expect(later.tap_received_at_ms).toBeGreaterThan(T0 + 4_000);
    expect(later.tap_since_ms).toBe(T0);
    expect(later.line_since_ms).toBe(T0);
    // Silent for longer than the tap counts as current, then back: a new run of arrivals.
    await vi.advanceTimersByTimeAsync(9_000);
    const back = Date.now();
    push(TAP);
    await vi.advanceTimersByTimeAsync(200);
    expect(store.getSnapshot().telemetry.tap_since_ms).toBe(back);
    store.dispose();
  });

  it('keeps the snapshot poll at its first answer, and restarts it after a failed read', async () => {
    const { store, failNextSnapshot } = await started();
    const since = store.getSnapshot().snapshotSinceMs ?? null;
    expect(since).not.toBeNull();
    await vi.advanceTimersByTimeAsync(10_500);
    expect(store.getSnapshot().view.snapshot_at_ms ?? 0).toBeGreaterThan(since ?? 0);
    expect(store.getSnapshot().snapshotSinceMs).toBe(since);
    failNextSnapshot();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(store.getSnapshot().sourceError).not.toBeNull();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(store.getSnapshot().sourceError).toBeNull();
    expect(store.getSnapshot().snapshotSinceMs ?? 0).toBeGreaterThan(since ?? 0);
    store.dispose();
  });
});
