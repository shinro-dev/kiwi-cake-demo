// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// READING THE CAKE RESIDENT STATUS: the version handshake, the refusals, and what a failed read must not do.
//
// The surface is versioned APART from the contract, so it is refused on its own terms: a runtime speaking a version
// this console does not know is refused rather than read optimistically, exactly as the contract handshake is.
//
// The property that matters most is the last one here. A read that fails must leave the last good status in place.
// Clearing it would turn one missed poll into a console reporting no resident, which is the same untruth the
// runtime's own retention exists to prevent, told one layer up.

import { describe, expect, it } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { HttpSource } from './http';
import type { FetchLike } from './http';
import { ConsoleStore } from '../store/store';

const STATUS = {
  cake_status_version: '1',
  configured: true,
  reachable: true,
  reason_code: null,
  stale: false,
  session_changed: null,
  clock_domain: 'laptop_monotonic',
  observation: {
    wall_utc: '2026-09-23T10:27:24.757Z',
    mono_ns: 1_000_000_000,
    age_ms: 40,
    resident: {
      session_uuid: '01a0cdba8c9c7b09b03c1f945f489104',
      build_identity: 'd3'.repeat(32),
      config_identity: '8b'.repeat(32),
      target_profile_digest: '84'.repeat(32),
      plan_digest: 'c9'.repeat(32),
    },
    counts: { epoch: 0, slots: 0, bindings: 0, resources: 0, replay: 0, snapshot_present: 1 },
    transaction_state: 'none',
    flight: { records: 3, oldest: 0, next: 2 },
  },
  activity: { value: false, evidence: 'the resident reports 0 slots' },
  mission_participation: { value: false, evidence: 'the controller has no Cake adapter' },
};

/** The reads ConsoleStore.start makes, with the Cake answer under the caller's control. */
function fetchWith(cake: () => { status: number; body: unknown }): { impl: FetchLike; urls: string[]; calls: { url: string; method: string }[] } {
  const urls: string[] = [];
  const calls: { url: string; method: string }[] = [];
  const impl: FetchLike = async (url: string, init?: RequestInit) => {
    urls.push(url);
    calls.push({ url, method: (init?.method ?? 'GET').toUpperCase() });
    if (url.includes('/cake')) {
      const answer = cake();
      return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body } as unknown as Response;
    }
    const body = url.includes('/hello')
      ? { ok: true, contract_version: '1', runtime_session: 'runtime-session-a', read_only: false, commands_enabled: true }
      : url.includes('/snapshot')
        ? runningSnapshot
        : url.includes('/history')
          ? { ok: true, runs: [] }
          : {};
    return { ok: true, status: 200, json: async () => body } as unknown as Response;
  };
  return { impl, urls, calls };
}

describe('reading the Cake status over HTTP', () => {
  it('reads a status the runtime serves', async () => {
    const { impl } = fetchWith(() => ({ status: 200, body: STATUS }));
    const answer = await new HttpSource({ fetchImpl: impl }).cakeStatus();
    expect(answer.ok).toBe(true);
    if (answer.ok) {
      expect(answer.status.observation?.resident.session_uuid).toBe(STATUS.observation.resident.session_uuid);
      expect(answer.status.reachable).toBe(true);
    }
  });

  it('refuses a version it does not speak rather than reading the fields anyway', async () => {
    const { impl } = fetchWith(() => ({ status: 200, body: { ...STATUS, cake_status_version: '2' } }));
    const answer = await new HttpSource({ fetchImpl: impl }).cakeStatus();
    expect(answer.ok).toBe(false);
    if (!answer.ok) expect(answer.reason_code).toContain('not a Cake status this console speaks');
  });

  it('reports a runtime that does not serve the route as one that does not report a resident', async () => {
    const { impl } = fetchWith(() => ({ status: 404, body: { ok: false, reason_code: 'not_found' } }));
    const answer = await new HttpSource({ fetchImpl: impl }).cakeStatus();
    expect(answer.ok).toBe(false);
    // NOT "a resident that answered with nothing". An older runtime has nothing to say, which is a different fact.
    if (!answer.ok) expect(answer.reason_code).toBe('this runtime does not report a Cake resident');
  });

  it('is a GET and asks for nothing else', async () => {
    const { impl, urls } = fetchWith(() => ({ status: 200, body: STATUS }));
    await new HttpSource({ fetchImpl: impl }).cakeStatus();
    expect(urls).toEqual(['/api/cake']);
  });
});

describe('the store holding the Cake status', () => {
  it('publishes the status it read', async () => {
    const { impl } = fetchWith(() => ({ status: 200, body: STATUS }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl: impl }));
    await store.start();
    store.dispose();
    expect(store.getSnapshot().cakeStatus?.reachable).toBe(true);
    expect(store.getSnapshot().cakeStatusError).toBeNull();
  });

  it('keeps the last good status when a later read fails, and says why', async () => {
    let fail = false;
    const { impl } = fetchWith(() => (fail ? { status: 503, body: { ok: false, reason_code: 'runtime_unavailable' } } : { status: 200, body: STATUS }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl: impl }));
    await store.start();
    expect(store.getSnapshot().cakeStatus).not.toBeNull();
    fail = true;
    await store.refreshCakeStatus();
    store.dispose();
    // The reading survives the failed poll. A console that blanked it would report no resident because of one
    // missed request, which is exactly the failure this surface is built to avoid.
    expect(store.getSnapshot().cakeStatus?.observation?.resident.session_uuid).toBe(STATUS.observation.resident.session_uuid);
    expect(store.getSnapshot().cakeStatusError).not.toBeNull();
  });

  it('never submits a command while reading it', async () => {
    const { impl, calls } = fetchWith(() => ({ status: 200, body: STATUS }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl: impl }));
    await store.start();
    await store.refreshCakeStatus();
    store.dispose();
    // Starting a tab against a runtime with a run in progress DOES read that run's command result by id, which is a
    // GET. What must never happen is a write: every request a page load and a status read make is a read.
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([]);
    expect(calls.some((call) => call.url === '/api/cake')).toBe(true);
  });
});
