// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MISSION'S RUNS, KEPT FOR THE PAGE'S LIFE (spec rules 3.8 and 6.3.1). The store keeps the timeline kinds of every
// mission run, read once from the run's retained records when it has finished and followed live while it runs, and never
// evicts them; its timeline inserts a key once and never removes one. The candidate list is read once per mission id,
// whatever the mission is doing, so a card can say what its candidate was expected to do after the wait is over. A link
// change (the runtime stops answering, then answers again) is an entry of its own.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, HistoryEventsResult, MissionCandidatesResult } from '../api/types';
import { ConsoleStore } from './store';

async function until(done: () => boolean, slices = 400): Promise<void> {
  for (let slice = 0; slice < slices && !done(); slice += 1) await vi.advanceTimersByTimeAsync(250);
}

function counting(source: SyntheticSource, asked: string[], candidateReads: { n: number }): ConsoleSource {
  const historyEvents = async (runId: string): Promise<HistoryEventsResult> => {
    asked.push(runId);
    return source.historyEvents(runId);
  };
  const missionCandidates = async (): Promise<MissionCandidatesResult> => {
    candidateReads.n += 1;
    return source.missionCandidates();
  };
  return new Proxy(source, {
    get(target, property, receiver) {
      if (property === 'historyEvents') return historyEvents;
      if (property === 'missionCandidates') return missionCandidates;
      const value = Reflect.get(target, property, receiver) as unknown;
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  }) as ConsoleSource;
}

describe("the store's mission runs and timeline", () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('reads each finished mission run once, keeps its timeline kinds, and lists its states on the timeline', async () => {
    const asked: string[] = [];
    const reads = { n: 0 };
    const store = new ConsoleStore(counting(new SyntheticSource({ scenario: 'one-block' }), asked, reads));
    await store.start();
    await store.startMission();
    await until(() => store.getSnapshot().mission?.state === 'WAITING_FOR_CAPABILITY');
    await vi.advanceTimersByTimeAsync(6000);
    const runId = store.getSnapshot().mission?.targets[0]?.runs[0] ?? '';
    const kept = store.getSnapshot().missionRunEvents[runId] ?? [];
    expect(kept.some((event) => event.kind === 'run.finished')).toBe(true);
    expect(kept.every((event) => event.kind !== 'observation.captured' && event.kind !== 'timing.span')).toBe(true);
    // Read with its end once: four more mission reads ask for it no more.
    const readsSoFar = asked.filter((id) => id === runId).length;
    expect(readsSoFar).toBeGreaterThan(0);
    await vi.advanceTimersByTimeAsync(20000);
    expect(asked.filter((id) => id === runId)).toHaveLength(readsSoFar);
    const keys = store.getSnapshot().timeline.map((entry) => entry.key);
    expect(keys).toContain(`run:${runId}`);
    expect(store.getSnapshot().timeline.some((entry) => entry.group === runId && entry.text.startsWith('SEARCH'))).toBe(true);
    store.dispose();
  });

  it('never removes a timeline key, even when the next mission lists none of the earlier runs', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }));
    await store.start();
    await store.startMission();
    await until(() => store.getSnapshot().mission?.state === 'COMPLETE');
    await vi.advanceTimersByTimeAsync(6000);
    const first = store.getSnapshot().timeline.map((entry) => entry.key);
    expect(first).toContain('mission:synthetic-mission-0001:ended');
    await store.startMission();
    await vi.advanceTimersByTimeAsync(6000);
    const second = store.getSnapshot().timeline.map((entry) => entry.key);
    for (const key of first) expect(second).toContain(key);
    expect(second).toContain('mission:synthetic-mission-0002:started');
    store.dispose();
  });

  it('reads the candidate list once per mission id, before and after the wait', async () => {
    const reads = { n: 0 };
    const store = new ConsoleStore(counting(new SyntheticSource({ scenario: 'one-block' }), [], reads));
    await store.start();
    await store.startMission();
    await vi.advanceTimersByTimeAsync(6000);
    expect(reads.n).toBe(1);
    expect(store.getSnapshot().missionCandidates?.map((item) => item.key)).toEqual(['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped']);
    await until(() => store.getSnapshot().mission?.state === 'WAITING_FOR_CAPABILITY');
    await vi.advanceTimersByTimeAsync(11000);
    expect(reads.n).toBe(1);
    store.dispose();
  });

  it('adds one link entry when the runtime stops answering and one when it answers again', async () => {
    const source = new SyntheticSource({ scenario: 'far-block-success' });
    let down = false;
    const flaky = new Proxy(source, {
      get(target, property, receiver) {
        if (property === 'snapshot') return async () => {
          if (down) throw new Error('the runtime did not answer');
          return target.snapshot();
        };
        const value = Reflect.get(target, property, receiver) as unknown;
        return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
      },
    }) as ConsoleSource;
    const store = new ConsoleStore(flaky);
    await store.start();
    down = true;
    await vi.advanceTimersByTimeAsync(11000);
    down = false;
    await vi.advanceTimersByTimeAsync(5000);
    const links = store.getSnapshot().timeline.filter((entry) => entry.kind === 'link');
    expect(links.map((entry) => entry.key)).toEqual(['link:1', 'link:2']);
    expect(links[0]?.text).toMatch(/^This display lost the runtime at \d{2}:\d{2}:\d{2}Z$/);
    expect(links[1]?.text).toMatch(/^This display reached the runtime again at /);
    store.dispose();
  });

  it("reads the finished-run list again while a finished mission run is missing from it, so the mission's end is known", async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }));
    await store.start();
    await store.startMission();
    await until(() => store.getSnapshot().mission?.state === 'COMPLETE');
    await vi.advanceTimersByTimeAsync(6000);
    const runId = store.getSnapshot().mission?.targets[0]?.runs[0] ?? '';
    const history = store.getSnapshot().history;
    const entry = history?.ok === true ? history.entries.find((item) => item.run_id === runId) : undefined;
    expect(entry?.finished_wall_utc).toMatch(/Z$/);
    expect(store.getSnapshot().timeline.find((item) => item.key === 'mission:synthetic-mission-0001:ended')?.text).toMatch(/^Mission complete, ended \d{2}:\d{2}:\d{2}Z$/);
    store.dispose();
  });
});
