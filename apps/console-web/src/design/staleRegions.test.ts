// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// STALE AND DISCONNECTED (spec sections 4.1, 4.4, 6.8 and 8.3). A source double whose reads fail after the first ones, as
// `api/reconnect.test.ts` builds one: the regions keep what they last read, the mission's marker prints the fixed UTC
// time of its last good read, the verdict says the runtime is not answering (no time: verify-r1 must_fix 4), Start is off, Stop stays
// on over the last snapshot, the timeline records the lost link once, and nothing ticks: a render 20 s later is
// byte-identical.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, EventsMessage } from '../api/types';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';

const FAILING = new Set(['snapshot', 'mission', 'robotStatus', 'history', 'cakeStatus', 'capabilityCheck', 'signatureCheck', 'missionCandidates']);

/** The synthetic source, whose reads throw and whose event stream goes quiet once `down.value` is set. */
function flaky(down: { value: boolean }): ConsoleSource {
  const source = new SyntheticSource({ scenario: 'far-block-success' });
  return new Proxy(source, {
    get(target, property, receiver) {
      if (typeof property === 'string' && FAILING.has(property)) {
        const read = Reflect.get(target, property, receiver) as () => Promise<unknown>;
        return async () => {
          if (down.value) throw new Error('the runtime did not answer');
          return read.call(target);
        };
      }
      if (property === 'subscribeEvents') {
        return (cursor: number, onMessage: (message: EventsMessage) => void) =>
          target.subscribeEvents(cursor, (message) => {
            if (!down.value) onMessage(message);
          });
      }
      const value = Reflect.get(target, property, receiver) as unknown;
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  }) as ConsoleSource;
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

describe('the demo screen when the runtime stops answering', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:21:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('keeps what it read, marks it with fixed times, turns Start off, keeps Stop on, and does not tick', async () => {
    const down = { value: false };
    const store = new ConsoleStore(flaky(down), { storage: null });
    await store.start();
    await store.startMission();
    await vi.advanceTimersByTimeAsync(3000);
    down.value = true;
    await vi.advanceTimersByTimeAsync(11000);

    const first = render(store);
    await vi.advanceTimersByTimeAsync(20000);
    const later = render(store);
    expect(later).toBe(first);

    // The content is kept, dimmed, and the mission's marker prints the fixed time of its last good read.
    expect(first).toContain('data-verdict="running"');
    expect(first).toMatch(/data-stale-marker="mission"[^>]*>Not read since \d{2}:\d{2}:\d{2}Z</);
    expect(first).toContain('opacity:0.6');
    // The verdict: not answering, with no time this page made up (verify-r1 must_fix 4); Start off, Stop on over the last snapshot.
    expect(first).toContain('data-readiness="not_ready"');
    expect(first).toContain('The runtime is not answering.');
    expect(first).not.toMatch(/not answering since/);
    expect(first).toMatch(/data-control="start"[^>]*aria-disabled="true"/);
    expect(first).toMatch(/data-control="stop"[^>]*aria-disabled="false"/);
    // The timeline records the lost link once, at its time.
    expect(first.match(/This display lost the runtime at \d{2}:\d{2}:\d{2}Z/g)?.length).toBe(1);
    expect(first).not.toMatch(/\bago\b/);
    store.dispose();
  });
});

/** The synthetic source, whose snapshot read never answers (no new snapshot, no error) once `hang.value` is set. */
function hanging(hang: { value: boolean }): ConsoleSource {
  const source = new SyntheticSource({ scenario: 'far-block-success' });
  return new Proxy(source, {
    get(target, property, receiver) {
      if (property === 'snapshot') {
        return () => (hang.value ? new Promise<never>(() => undefined) : target.snapshot());
      }
      const value = Reflect.get(target, property, receiver) as unknown;
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  }) as ConsoleSource;
}

describe('the demo screen when the snapshot read hangs', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:21:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('prints the fixed time the last snapshot reached this page, and renders 3 s and 20 s apart are byte-identical', async () => {
    const hang = { value: false };
    const store = new ConsoleStore(hanging(hang), { storage: null });
    await store.start();
    hang.value = true;
    await vi.advanceTimersByTimeAsync(16000);

    const first = render(store);
    await vi.advanceTimersByTimeAsync(3000);
    const soon = render(store);
    await vi.advanceTimersByTimeAsync(20000);
    const later = render(store);
    expect(soon).toBe(first);
    expect(later).toBe(first);

    // The readiness detail names the snapshot's fixed time, never its age.
    expect(first).toMatch(/The runtime&#x27;s last snapshot reached this page at \d{2}:\d{2}:\d{2}Z\./);
    expect(first).not.toMatch(/\bs old\b|\bago\b/);
    store.dispose();
  });
});
