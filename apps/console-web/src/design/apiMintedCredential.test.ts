// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A TAB OPENED WHILE THE RUNTIME WAS DOWN, ON A CONSOLE API THAT MINTED ITS OWN CREDENTIAL (redesign r4 verify-r1
// must_fix 2).
//
// With no runtime to name a session, the console API mints a credential of its own and says so in the handshake
// (`credential_owner: api_process`, no bound session). When a runtime comes up the API binds to its session and serves
// that session's credential from then on (`credential_owner: runtime_session`), and the value this tab was served is
// refused `credential_superseded` on every command, Stop included. The tab learns that from the handshake, before any
// press: commands off with the reload named, readiness not Ready, Stop drawn as "Reload to stop". A refusal by the
// console server is said in the server's voice, never "The runtime refused it": nothing reached the runtime.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import type { Command } from '@kiwi/demo-contracts';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { readScreen, textOf } from './demoScenarioPlay';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function buttonTag(markup: string, control: string): string {
  return new RegExp(`<button[^>]*data-control="${control}"[^>]*>`).exec(markup)?.[0] ?? '(not drawn)';
}

function reasonOf(markup: string, control: string): string {
  return textOf(new RegExp(`<p[^>]*data-control-reason="${control}"[^>]*>[\\s\\S]*?</p>`).exec(markup)?.[0] ?? '');
}

const HELLO_DOWN = {
  ok: false, reason_code: 'runtime_unavailable', contract_version: '1', read_only: true, commands_enabled: false, runtime_session: null,
  credential_owner: 'api_process', bound_runtime_session: null, runtime: { reachable: false, session: null, contract_version: null, data_mode: null },
};

const HELLO_UP = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: 'synthetic-session-0001',
  credential_owner: 'runtime_session', bound_runtime_session: 'synthetic-session-0001',
  runtime: { reachable: true, session: 'synthetic-session-0001', contract_version: '1', data_mode: 'live' },
};

const IDLE_SERVING_SCAN = {
  ...idleSnapshot,
  runtime_session: 'synthetic-session-0001',
  data_mode: 'live',
  capabilities: [
    ...idleSnapshot.capabilities.filter((item) => item.id !== 'scan_scene' && item.id !== 'stop'),
    { id: 'scan_scene', state: 'enabled', reason_code: null, reason: null, depends_on: null },
    { id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', depends_on: null },
  ],
};

function harness(): { up: { value: boolean }; posts: string[]; source: HttpSource } {
  const up = { value: false };
  const posts: string[] = [];
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      posts.push(`${method} ${input}`);
      return json(403, { ok: false, reason_code: 'credential_superseded' });
    }
    if (input.startsWith('/api/hello')) return json(200, up.value ? HELLO_UP : HELLO_DOWN);
    if (!up.value) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
    if (input === '/api/snapshot') return json(200, IDLE_SERVING_SCAN);
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  return { up, posts, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'api-minted-test' }) };
}

describe('a tab opened on a console API that minted its own credential, before any runtime answered', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T05:10:00.000Z') }));
  afterEach(() => vi.useRealTimers());

  it('learns from the handshake, before any press, that the server no longer accepts its credential', async () => {
    const { up, posts, source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    up.value = true;
    await vi.advanceTimersByTimeAsync(12_000);

    const markup = render(store);
    const session = store.getSnapshot().session;
    expect(session.credential_superseded).toBe(true);
    expect(session.runtime_replaced).toBe(true);
    // A session that never answered this tab is not adopted as the one it booted against.
    expect(session.booted_runtime_session).toBeNull();
    expect(store.getSnapshot().view.commands_enabled).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('credential_superseded');

    expect(buttonTag(markup, 'scan_scene')).toContain('aria-disabled="true"');
    expect(reasonOf(markup, 'scan_scene')).toMatch(/reload/i);
    expect(readScreen(markup, store).readiness).not.toBe('ready');
    const stop = buttonTag(markup, 'stop');
    expect(stop).toContain('aria-disabled="false"');
    // Round 7 item 3: the stale Stop takes over once on its press (it pinned "Reload to stop" before any press).
    expect(stop).toContain('data-stop-takeover=""');
    // No claim of readiness established against a session that never answered this tab.
    expect(textOf(markup)).not.toMatch(/established readiness against/);
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('says a refusal by the console server in the server\'s voice, never "The runtime refused it"', async () => {
    const { up, posts, source } = harness();
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(3000);
    up.value = true;
    await vi.advanceTimersByTimeAsync(12_000);

    // Presses through the store, as a stale control would send them: both are refused before the runtime.
    await store.submit({ type: 'scan_scene', args: { plan_only: false } } as unknown as Command);
    await store.requestStop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(posts).toEqual(['POST /api/commands', 'POST /api/stop']);
    const words = textOf(render(store));
    expect(words).not.toMatch(/The runtime refused it/);
    expect(words).toMatch(/The console server refused it: credential_superseded\. Nothing reached the runtime\./);
    expect(words).toMatch(/Stop refused by the console server: credential_superseded\. Nothing reached the runtime/);
    store.dispose();
  });
});
