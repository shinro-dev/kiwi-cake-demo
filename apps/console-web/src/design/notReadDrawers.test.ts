// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// EVIDENCE AND THE CAKE TAB SAY "NOT READ" WHILE THE RUNTIME HAS NOT ANSWERED THIS DISPLAY (redesign r4 verify-r2
// must_fix 4).
//
// With the runtime not answering, the console API answers the handshake itself (ok false, runtime_unavailable) and every
// read is 503. Nothing about the runtime's runs or the robot's resident was read, yet Evidence printed "No decision frame
// is pinned", "0 predicates" and "No predicate has been recorded in this run", and the Cake tab listed the controller
// under "Modules the resident holds". Each is now said as not read, with the read's code; the controller's card is
// under its own heading in every state. Once the runtime answers, the drawers read the record again.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { robotBody } from '../model/robotStatusFixture';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Drawer } from './Drawers';
import { element, textOf } from './demoScenarioPlay';

/** The console API's handshake while the runtime is down, in the shape 8771 served on 2026-09-28 at 05:55Z. */
const HELLO_DOWN = {
  ok: false, reason_code: 'runtime_unavailable', contract_version: '1', read_only: true, commands_enabled: false, runtime_session: null,
  credential_owner: 'runtime_session', bound_runtime_session: 'synthetic-session-0000', api: { session: 'api-0123456789ab', version: '0.1.0' },
  runtime: { reachable: false, session: null, contract_version: null, data_mode: null },
};

const HELLO_UP = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: 'synthetic-session-0000',
  credential_owner: 'runtime_session', bound_runtime_session: 'synthetic-session-0000', api: { session: 'api-0123456789ab', version: '0.1.0' },
  runtime: { reachable: true, session: 'synthetic-session-0000', contract_version: '1', data_mode: 'live' },
};

const EMPTY_RECORD = ['No decision frame is pinned', '0 predicates', 'No predicate has been recorded in this run'];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function harness(): { up: { value: boolean }; posts: string[]; source: HttpSource } {
  const up = { value: false };
  const posts: string[] = [];
  const fetchImpl: FetchLike = async (input, init) => {
    if ((init?.method ?? 'GET').toUpperCase() !== 'GET') {
      posts.push(input);
      return json(503, { ok: false, reason_code: 'runtime_unavailable' });
    }
    if (input.startsWith('/api/hello')) return json(200, up.value ? HELLO_UP : HELLO_DOWN);
    if (!up.value) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
    if (input === '/api/snapshot') return json(200, { ...idleSnapshot, runtime_session: 'synthetic-session-0000', data_mode: 'live' });
    if (input === '/api/robot') return json(200, robotBody());
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => {
    const stream: EventSourceLike = { onmessage: null, onerror: null, close: () => undefined };
    if (!up.value) {
      setTimeout(() => {
        stream.onmessage?.({ data: '{"stream_ended":{"reason_code":"runtime_unavailable"}}' });
        stream.onerror?.({});
      }, 10);
    }
    return stream;
  };
  return { up, posts, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'not-read-drawers-test' }) };
}

function drawer(store: ConsoleStore, id: 'evidence' | 'cake'): string {
  const snapshot = store.getSnapshot();
  const mode = snapshot.replay !== null ? 'replay' : snapshot.view.data_mode;
  return renderToStaticMarkup(
    createElement(StoreContext, { value: store }, createElement(Drawer, { id, snapshot, store, mode, onSelect: () => undefined, onClose: () => undefined })),
  );
}

describe('Evidence and the Cake tab while the runtime has not answered this display', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T06:00:00.000Z') }));
  afterEach(() => vi.useRealTimers());

  it('fresh load, +23 s: no empty-record sentence, "not read" with the code, and no module card under the resident', async () => {
    const { posts, source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(23_000);

    const evidence = textOf(drawer(store, 'evidence'));
    for (const phrase of EMPTY_RECORD) expect(evidence, phrase).not.toContain(phrase);
    expect(evidence).toMatch(/Not read: no run of this runtime has reached this display \([^)]*runtime_unavailable[^)]*\)/);
    // The drawer's scope line does not say there is no mission when the mission was not read.
    expect(evidence).not.toContain('mission: no mission');
    expect(evidence).toContain('mission: not read (runtime_unavailable)');

    const cake = drawer(store, 'cake');
    const modules = element(cake, 'data-cake-section="modules"') ?? '';
    expect(textOf(modules)).toMatch(/not read/);
    expect(textOf(modules)).toContain('runtime_unavailable');
    expect(modules).not.toContain('data-cake-module=');
    // The controller is this runtime's child and not the resident's: never under the resident's heading.
    expect(textOf(modules)).not.toContain('controller');
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('once the runtime answers, reads the record again, and the controller card stays under its own heading', async () => {
    const { up, source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    up.value = true;
    await vi.advanceTimersByTimeAsync(12_000);

    const evidence = textOf(drawer(store, 'evidence'));
    expect(evidence).not.toMatch(/Not read: no run of this runtime/);
    expect(evidence).toContain('No decision frame is pinned');
    const cake = drawer(store, 'cake');
    const modules = element(cake, 'data-cake-section="modules"') ?? '';
    expect(modules).toContain('data-cake-module=');
    expect(modules).not.toContain('data-cake-module="controller"');
    expect(textOf(modules)).not.toMatch(/not read/);
    expect(element(cake, 'data-cake-section="controller"') ?? '').toContain('data-cake-module="controller"');
    store.dispose();
  });
});
