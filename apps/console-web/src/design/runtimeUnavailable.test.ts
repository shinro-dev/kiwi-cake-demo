// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A PAGE OPENED WHILE THE RUNTIME DOES NOT ANSWER (verdict 2026-09-27 must_fix 1; spec 5.2 and 4.3). The console API
// answers the handshake with ok false and reason_code runtime_unavailable, and every other read with 503. That is a
// runtime that has not answered, not a contract this console cannot read and not a rehearsal: the mode mark reads "Mode
// not recorded", nothing says SIMULATION, rehearsal or contract version, and when the runtime answers again the page
// recovers without a reload: the handshake is asked again, the contract is compatible, and Stop is on when the runtime
// serves it on.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { textOf } from './demoScenarioPlay';

/** What the console API answers the handshake with while no runtime answers it (console_api/app.py handle_hello). */
const HELLO_DOWN = {
  ok: false,
  reason_code: 'runtime_unavailable',
  contract_version: '1',
  read_only: true,
  commands_enabled: false,
  runtime_session: null,
  credential_owner: null,
  bound_runtime_session: null,
  runtime: { reachable: false, session: null, contract_version: null, data_mode: null },
};

const HELLO_UP = {
  ok: true,
  reason_code: null,
  contract_version: '1',
  read_only: false,
  commands_enabled: true,
  runtime_session: 'synthetic-session-0001',
  credential_owner: 'runtime_session',
  runtime: { reachable: true, session: 'synthetic-session-0001', contract_version: '1', data_mode: 'live' },
};

/** A live runtime with a run going, which serves Stop enabled. */
const LIVE_RUNNING = {
  ...runningSnapshot,
  data_mode: 'live',
  capabilities: [...runningSnapshot.capabilities.filter((item) => item.id !== 'stop'), { id: 'stop', state: 'enabled', reason_code: null, reason: null, depends_on: null }],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function harness(boundSession: string | null = null): { down: { value: boolean }; posts: string[]; hellos: () => number; source: HttpSource } {
  const down = { value: true };
  const posts: string[] = [];
  let hellos = 0;
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      posts.push(`${method} ${input}`);
      return json(599, {});
    }
    if (input.startsWith('/api/hello')) {
      hellos += 1;
      return json(200, down.value ? { ...HELLO_DOWN, bound_runtime_session: boundSession } : HELLO_UP);
    }
    if (down.value) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
    if (input === '/api/snapshot') return json(200, LIVE_RUNNING);
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => {
    const stream: EventSourceLike = { onmessage: null, onerror: null, close: () => undefined };
    if (down.value) setTimeout(() => stream.onerror?.({}), 10);
    return stream;
  };
  return { down, posts, hellos: () => hellos, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'runtime-away-test' }) };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function buttonTag(markup: string, control: string): string {
  return new RegExp(`<button[^>]*data-control="${control}"[^>]*>`).exec(markup)?.[0] ?? '(not drawn)';
}

describe('a page opened while the runtime does not answer', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T20:06:00.000Z') }));
  afterEach(() => vi.useRealTimers());

  it('says the mode is not recorded, claims no rehearsal and no contract mismatch, and recovers when the runtime answers', async () => {
    const { down, posts, hellos, source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);

    const away = render(store);
    expect(/data-mode-mark="([a-z_]+)"/.exec(away)?.[1]).toBe('not_recorded');
    const words = textOf(away);
    expect(words).not.toMatch(/SIMULATION/);
    expect(words).not.toMatch(/rehearsal/i);
    expect(words).not.toMatch(/contract version/);
    expect(store.getSnapshot().view.contract_compatible).toBe(true);
    expect(store.getSnapshot().view.data_mode).toBeNull();
    // The runtime is not answering, and that is what Start says: with no time, since this page's first failed read is
    // not the runtime's time and a reload moved it (verify-r1 must_fix 4).
    expect(buttonTag(away, 'start')).toContain('aria-disabled="true"');
    expect(words).toContain('The runtime is not answering.');
    expect(words).not.toMatch(/not answering since/);

    // The runtime comes back with a run going and serves Stop on.
    down.value = false;
    const askedBefore = hellos();
    await vi.advanceTimersByTimeAsync(12_000);

    const back = render(store);
    expect(hellos()).toBeGreaterThan(askedBefore);
    expect(store.getSnapshot().view.contract_compatible).toBe(true);
    expect(store.getSnapshot().view.commands_enabled).toBe(true);
    expect(/data-mode-mark="([a-z_]+)"/.exec(back)?.[1]).toBe('live');
    expect(textOf(back)).not.toMatch(/contract version/);
    expect(buttonTag(back, 'stop')).toContain('aria-disabled="false"');
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('says the runtime was replaced when it comes back under another session than the one this page was served for', async () => {
    const { down, posts, source } = harness('synthetic-session-0000');
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    down.value = false;
    await vi.advanceTimersByTimeAsync(12_000);

    const back = render(store);
    const snap = store.getSnapshot();
    expect(snap.view.contract_compatible).toBe(true);
    expect(snap.session.runtime_replaced).toBe(true);
    expect(snap.view.commands_enabled).toBe(false);
    expect(snap.view.read_only_reason).toBe('runtime_session_changed');
    // Stop is never taken away by a replaced session, and never drawn as a stop the console server refuses: the API
    // rotated its credential to the new session, so the control stays on and its press takes over once with the served
    // page's credential (round 7 item 3; it pinned "Reload to stop" before any press, verify-r1 must_fix 1).
    const stop = buttonTag(back, 'stop');
    expect(stop).toContain('aria-disabled="false"');
    expect(stop).toContain('data-stop-takeover=""');
    expect(stop).not.toContain('Ask the runtime to stop');
    expect(/title="([^"]*)"/.exec(stop)?.[1]).toMatch(/sends Stop once more/);
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('still marks a real contract mismatch as one, and follows nothing', async () => {
    const calls: string[] = [];
    const fetchImpl: FetchLike = async (input) => {
      calls.push(input);
      return json(200, { ok: false, reason_code: 'contract_incompatible', contract_version: '1', read_only: true, commands_enabled: false });
    };
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null, boot: null, sessionId: 'mismatch-test' }), { storage: null });
    await store.start();
    expect(store.getSnapshot().view.contract_compatible).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('contract_incompatible');
    expect(calls.every((call) => call.startsWith('/api/hello'))).toBe(true);
    store.dispose();
  });
});
