// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, MissionStartOutcome } from '../api/types';
import { ConsoleStore } from './store';

function sourceWith(retry: ((missionId: string) => Promise<MissionStartOutcome>) | undefined): ConsoleSource {
  // The synthetic source offers a retry of its own (track U2), so a source WITHOUT one is the synthetic source with that
  // method masked by an own property that is not a function.
  const base = new SyntheticSource();
  return Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, { missionRetry: retry });
}

describe('Try again, from the store', () => {
  it('says whether the source offers a retry at all', () => {
    expect(new ConsoleStore(sourceWith(undefined)).retryOffered).toBe(false);
    expect(new ConsoleStore(sourceWith(async () => ({ kind: 'started', mission_id: 'm' }))).retryOffered).toBe(true);
  });

  it('sends one retry, keeps the receipt, and refuses a second press while one is in flight', async () => {
    let calls = 0;
    let release: (value: MissionStartOutcome) => void = () => undefined;
    const store = new ConsoleStore(
      sourceWith(() => {
        calls += 1;
        return new Promise<MissionStartOutcome>((resolve) => {
          release = resolve;
        });
      }),
    );
    const first = store.retryMission('m');
    expect(store.getSnapshot().retryingMission).toBe(true);
    const second = await store.retryMission('m');
    expect(second.kind).toBe('refused');
    expect(calls).toBe(1);
    release({ kind: 'started', mission_id: 'm' });
    expect((await first).kind).toBe('started');
    expect(store.getSnapshot().retryingMission).toBe(false);
    expect(store.getSnapshot().lastMissionRetry).toEqual({ kind: 'started', mission_id: 'm' });
  });

  it('passes the mission id of the hold through to the source', async () => {
    const seen: string[] = [];
    const store = new ConsoleStore(
      sourceWith(async (missionId) => {
        seen.push(missionId);
        return { kind: 'started', mission_id: missionId };
      }),
    );
    await store.retryMission('mission-0001');
    expect(seen).toEqual(['mission-0001']);
  });

  it('refuses without sending on a source that has no retry', async () => {
    const store = new ConsoleStore(sourceWith(undefined));
    const outcome = await store.retryMission('m');
    expect(outcome).toEqual({ kind: 'refused', reason_code: 'this runtime does not offer a mission retry', detail: null });
  });
});
