// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A HANDSHAKE THE RUNTIME HAS NOT ANSWERED IS ITS OWN STATE (redesign r4 verify-r1 must_fix 3).
//
// While the runtime is down the console API answers the handshake itself: ok false, runtime_unavailable,
// commands_enabled false, no runtime contract version; every read is 503. Nothing about the runtime was read, so the
// page states nothing about it: the contract is not known, commands are off by the API's own answer, whether a command
// is of unknown execution is not known, and Diagnostics says "not read" instead of an empty record. Once the runtime
// answers, the contract and commands items read met again. In the in-page simulation the same items say the simulation
// answered, with no runtime and no server.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { Drawer, DRAWER_ORDER } from './Drawers';
import { settle, textOf } from './demoScenarioPlay';

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

const NOT_READ_LIES = [
  'allows this page to command',
  'speaks the contract this console reads',
  'Contract compatible',
  'Commands from this tab allowed',
  'No run has finished in this runtime session',
  "No check is on this runtime's record",
  'This mission has recorded no activation',
  'no run has been recorded yet',
  'never_arrived',
];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function harness(): { up: { value: boolean }; source: HttpSource } {
  const up = { value: false };
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
  return { up, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'handshake-unanswered-test' }) };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function drawer(store: ConsoleStore, id: (typeof DRAWER_ORDER)[number]): string {
  const snapshot = store.getSnapshot();
  const mode = snapshot.replay !== null ? 'replay' : snapshot.view.data_mode;
  return renderToStaticMarkup(
    createElement(StoreContext, { value: store }, createElement(Drawer, { id, snapshot, store, mode, onSelect: () => undefined, onClose: () => undefined })),
  );
}

function readyState(markup: string, item: string): string | null {
  return new RegExp(`data-ready-item="${item}" data-ready-state="([a-z_]+)"`).exec(markup)?.[1] ?? null;
}

function readyLine(markup: string, item: string): string {
  return textOf(new RegExp(`<li data-ready-item="${item}"[\\s\\S]*?</li>`).exec(markup)?.[0] ?? '');
}

describe('a page whose handshake the runtime has not answered', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T20:06:00.000Z') }));
  afterEach(() => vi.useRealTimers());

  it('states no runtime fact it never read, on the main screen or in any Diagnostics tab', async () => {
    const { source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);

    const main = render(store);
    for (const item of ['contract', 'commands', 'uncertain']) expect(readyState(main, item), item).not.toBe('met');
    expect(readyLine(main, 'contract')).toMatch(/has not answered the handshake/);
    expect(readyLine(main, 'contract')).toMatch(/runtime_unavailable/);
    // The API's own answer: commands are off, and the store says so too.
    expect(store.getSnapshot().view.commands_enabled).toBe(false);
    const texts = [textOf(main), ...DRAWER_ORDER.map((id) => textOf(drawer(store, id)))];
    for (const text of texts) for (const lie of NOT_READ_LIES) expect(text, lie).not.toContain(lie);
    // No "synthetic" badge on a Cake view no snapshot stated.
    expect(textOf(drawer(store, 'connections'))).not.toMatch(/\bsynthetic\b/);
    store.dispose();
  });

  it('reads contract and commands met once the runtime answers', async () => {
    const { up, source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    up.value = true;
    await vi.advanceTimersByTimeAsync(12_000);
    const main = render(store);
    expect(readyState(main, 'contract')).toBe('met');
    expect(readyState(main, 'commands')).toBe('met');
    expect(readyState(main, 'uncertain')).toBe('met');
    expect(store.getSnapshot().view.commands_enabled).toBe(true);
    store.dispose();
  });
});

describe('the in-page simulation', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00.000Z') }));
  afterEach(() => vi.useRealTimers());

  it('says the simulation answered, with no runtime and no server', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: scenarioFrom(scenarioUrl('one-block')) }), { storage: null });
    await store.start();
    await settle(store);
    const main = render(store);
    for (const item of ['contract', 'commands', 'uncertain']) {
      const line = readyLine(main, item);
      expect(readyState(main, item), item).toBe('met');
      expect(line, item).toMatch(/simulation/);
      expect(line, item).not.toMatch(/\bruntime\b|\bserver\b/);
    }
    store.dispose();
  });
});
