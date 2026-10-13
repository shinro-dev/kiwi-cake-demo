// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// "THE RUNTIME IS NOT ANSWERING" CARRIES NO TIME THIS PAGE MADE UP (redesign r4 verify-r1 must_fix 4).
//
// The time of this page's first failed read is not the runtime's time: two loads over the same answers printed two
// different "since" times, and a page that never reached the runtime said it "lost" it. The readiness sentence and every
// control's reason say "The runtime is not answering." with no time, so a reload draws the same screen. A display that
// did reach the runtime keeps its own observation on the timeline in its own voice ("This display lost the runtime at
// ..."); one that never did says the runtime has not answered it, never "lost" or "reached again", and the strip says
// nothing has arrived.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { textOf } from './demoScenarioPlay';

/** The console API's handshake while the runtime is down, in the shape it served on 2026-09-27 at 20:05Z. */
const HELLO_DOWN = {
  ok: false, reason_code: 'runtime_unavailable', contract_version: '1', read_only: true, commands_enabled: false, runtime_session: null,
  credential_owner: 'runtime_session', bound_runtime_session: 'synthetic-session-0000',
  runtime: { reachable: false, session: null, contract_version: null, data_mode: null },
};

const HELLO_UP = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: 'synthetic-session-0000',
  credential_owner: 'runtime_session', bound_runtime_session: 'synthetic-session-0000',
  runtime: { reachable: true, session: 'synthetic-session-0000', contract_version: '1', data_mode: 'live' },
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function harness(upAtStart: boolean): { up: { value: boolean }; source: HttpSource } {
  const up = { value: upAtStart };
  const fetchImpl: FetchLike = async (input, init) => {
    if ((init?.method ?? 'GET').toUpperCase() !== 'GET') return json(599, {});
    if (input.startsWith('/api/hello')) return json(200, up.value ? HELLO_UP : HELLO_DOWN);
    if (!up.value) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
    if (input === '/api/snapshot') return json(200, { ...idleSnapshot, runtime_session: 'synthetic-session-0000', data_mode: 'live' });
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => {
    const stream: EventSourceLike = { onmessage: null, onerror: null, close: () => undefined };
    if (!up.value) setTimeout(() => stream.onerror?.({}), 10);
    return stream;
  };
  return { up, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'not-answering-test' }) };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

async function freshLoad(at: string): Promise<string> {
  vi.useFakeTimers({ now: new Date(at) });
  const store = new ConsoleStore(harness(false).source, { storage: null });
  await store.start();
  await vi.advanceTimersByTimeAsync(3000);
  const words = textOf(render(store));
  store.dispose();
  vi.useRealTimers();
  return words;
}

describe('a runtime that is not answering', () => {
  afterEach(() => vi.useRealTimers());

  it('draws the same main screen on two loads 28 s apart, and a page that never reached it says so', async () => {
    const first = await freshLoad('2026-09-27T20:06:00.000Z');
    const second = await freshLoad('2026-09-27T20:06:28.000Z');
    expect(second).toBe(first);
    expect(first).toContain('The runtime is not answering.');
    expect(first).not.toMatch(/not answering since/);
    expect(first).not.toMatch(/lost the runtime/);
    expect(first).not.toMatch(/reached the runtime again/);
    expect(first).not.toMatch(/last state that did arrive/);
    expect(first).toContain('The runtime has not answered this display.');
  });

  it('keeps a display that did reach the runtime saying, in its own voice, when it lost it; the readiness sentence has no time', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-27T20:00:00.000Z') });
    const { up, source } = harness(true);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    up.value = false;
    await vi.advanceTimersByTimeAsync(6000);
    const words = textOf(render(store));
    expect(words).toMatch(/This display lost the runtime at \d{2}:\d{2}:\d{2}Z/);
    expect(words).toContain('The runtime is not answering.');
    expect(words).not.toMatch(/not answering since/);
    expect(words).not.toContain('The runtime has not answered this display.');
    store.dispose();
  });
});
