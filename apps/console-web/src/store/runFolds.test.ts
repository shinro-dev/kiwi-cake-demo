// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE TIMELINE PER TARGET needs every run of the target folded, not only the one hydrated run. The store keeps a
// bounded cache of folded runs, read through the same history path, never polled, and never used for eligibility.

import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, HistoryEventsResult } from '../api/types';
import { enter, finished, started } from '../model/stageFixtures';
import { ConsoleStore, RUN_FOLDS_MAX } from './store';

function sourceServing(runs: Record<string, boolean>, asked: string[]): ConsoleSource {
  const base = new SyntheticSource();
  const historyEvents = async (runId: string): Promise<HistoryEventsResult> => {
    asked.push(runId);
    if (runs[runId] !== true) return { ok: false, reason_code: 'run_not_found' };
    return {
      ok: true,
      run_id: runId,
      events: [started(runId), enter(runId, 'SEARCH'), finished(runId, 'failed', { code: 'search_no_cube', phase: 'SEARCH', detail: 'no cube', measurements: [], frames: [] })],
      origins: [],
    };
  };
  return Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, { historyEvents });
}

describe('the per-run fold cache', () => {
  it('folds each named run once, and keeps what it folded', async () => {
    const asked: string[] = [];
    const store = new ConsoleStore(sourceServing({ a: true, b: true }, asked));
    await store.foldRuns(['a', 'b']);
    await store.foldRuns(['a', 'b']);
    expect(asked).toEqual(['a', 'b']);
    expect(store.getSnapshot().runFolds['a']?.outcome.reason_code).toBe('search_no_cube');
    expect(Object.keys(store.getSnapshot().runFolds).sort()).toEqual(['a', 'b']);
  });

  it('asks again for nothing it could not read in the same breath, and records nothing for it', async () => {
    const asked: string[] = [];
    const store = new ConsoleStore(sourceServing({}, asked));
    await store.foldRuns(['missing']);
    expect(store.getSnapshot().runFolds['missing']).toBeUndefined();
  });

  it('is bounded, dropping the oldest fold first', async () => {
    const ids = Array.from({ length: RUN_FOLDS_MAX + 2 }, (_, index) => `run-${String(index)}`);
    const store = new ConsoleStore(sourceServing(Object.fromEntries(ids.map((id) => [id, true])), []));
    for (const id of ids) await store.foldRuns([id]);
    const kept = Object.keys(store.getSnapshot().runFolds);
    expect(kept.length).toBe(RUN_FOLDS_MAX);
    expect(kept).not.toContain('run-0');
    expect(kept).toContain(`run-${String(RUN_FOLDS_MAX + 1)}`);
  });
});
