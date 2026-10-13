// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FIRST PAGE SERVED AFTER A RUNTIME RESTART (redesign r4 verify-r2 must_fix 1).
//
// The console API keeps one credential per runtime session and rotates it when it follows a new session. A page served
// before the API has followed the new one carries the credential of the session before it, and that page's own first
// handshake is what makes the API follow the new session: from then on the page's credential is refused
// `credential_superseded`, Stop included. The API names, in the page's boot block, the session the credential it served
// belongs to (`bound_runtime_session`); when the first answered handshake says the API is bound to another session, the
// page knows before any press that the server no longer accepts its credential. Stop is "Reload to stop", every other
// control is off with the reload named, and nothing says the server refused anything, because nothing was sent.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { parseBoot } from '../api/boot';
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

function stopButton(markup: string): string {
  return /<button[^>]*data-control="stop"[^>]*>[\s\S]*?<\/button>/.exec(markup)?.[0] ?? '(not drawn)';
}

function titleOf(tag: string): string {
  return (/title="([^"]*)"/.exec(tag)?.[1] ?? '').replace(/&#x27;/g, "'");
}

function reasonOf(markup: string, control: string): string {
  return textOf(new RegExp(`<p[^>]*data-control-reason="${control}"[^>]*>[\\s\\S]*?</p>`).exec(markup)?.[0] ?? '');
}

const S1 = 'synthetic-session-0001';
const S2 = 'synthetic-session-0002';

const RUNNING = {
  ...runningSnapshot,
  data_mode: 'live',
  capabilities: [...runningSnapshot.capabilities.filter((item) => item.id !== 'stop'), { id: 'stop', state: 'enabled', reason_code: null, reason: null, depends_on: null }],
};

const IDLE_SERVING_SCAN = {
  ...idleSnapshot,
  data_mode: 'live',
  capabilities: [
    ...idleSnapshot.capabilities.filter((item) => item.id !== 'scan_scene' && item.id !== 'stop'),
    { id: 'scan_scene', state: 'enabled', reason_code: null, reason: null, depends_on: null },
    { id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', depends_on: null },
  ],
};

function helloOf(session: string): Record<string, unknown> {
  return {
    ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: session,
    credential_owner: 'runtime_session', bound_runtime_session: session, runtime: { reachable: true, session, contract_version: '1', data_mode: 'live' },
  };
}

/** A page served with `served` (the boot block's session and owner), over a console API bound to `answering`. */
function harness(served: { bound_runtime_session: string | null; credential_owner: string }, answering: string, snapshot: Record<string, unknown>): { posts: string[]; source: HttpSource } {
  const posts: string[] = [];
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      posts.push(`${method} ${input}`);
      return json(403, { ok: false, reason_code: 'credential_superseded' });
    }
    if (input.startsWith('/api/hello')) return json(200, helloOf(answering));
    if (input === '/api/snapshot') return json(200, { ...snapshot, runtime_session: answering });
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  const boot = parseBoot({
    contract_version: '1', api_base: '/api', api_session: 'api-0123456789ab', credential: 'served-' + 'credential-value',
    credential_header: 'X-Console-Credential', request_header: 'X-Console-Request', ...served,
  });
  return { posts, source: new HttpSource({ fetchImpl, eventSourceFactory, boot, sessionId: 'first-page-test' }) };
}

describe('the first page served after a runtime restart', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') }));
  afterEach(() => vi.useRealTimers());

  // Round 7 item 3: these cases pinned "Reload to stop" (a press reloads and sends no command) before any press. A stale
  // tab's Stop now takes over once on its press, so the first press stops the run; they pin that Stop instead.
  it('served for S1 while the first handshake says the server is bound to S2: the taking-over Stop and no command offered, before any press', async () => {
    const { posts, source } = harness({ bound_runtime_session: S1, credential_owner: 'runtime_session' }, S2, RUNNING);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1000);

    const session = store.getSnapshot().session;
    expect(session.credential_superseded).toBe(true);
    expect(session.runtime_replaced).toBe(true);
    expect(store.getSnapshot().view.commands_enabled).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('credential_superseded');

    const markup = render(store);
    const stop = stopButton(markup);
    expect(stop).toContain('aria-disabled="false"');
    expect(stop).toContain('data-stop-takeover=""');
    expect(stop).not.toContain('data-stop-reload');
    expect(textOf(stop)).toBe('Stop');
    expect(titleOf(stop)).toMatch(/as a reload does, and sends Stop once more/);
    expect(titleOf(stop)).not.toMatch(/would be refused/);
    expect(titleOf(stop)).toContain(S1);
    expect(titleOf(stop)).toContain(S2);
    // Nothing was sent, so nothing says the server refused anything.
    expect(titleOf(stop)).not.toMatch(/refused this tab/);
    expect(buttonTag(markup, 'scan_scene')).toContain('aria-disabled="true"');
    expect(readScreen(markup, store).readiness).not.toBe('ready');
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('idle and serving Scan scene under S2: Scan scene is off with the reload named, Stop is the taking-over Stop', async () => {
    const { posts, source } = harness({ bound_runtime_session: S1, credential_owner: 'runtime_session' }, S2, IDLE_SERVING_SCAN);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1000);
    const markup = render(store);
    expect(buttonTag(markup, 'scan_scene')).toContain('aria-disabled="true"');
    expect(reasonOf(markup, 'scan_scene')).toMatch(/reload/i);
    expect(stopButton(markup)).toContain('data-stop-takeover=""');
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('served before any runtime named a session, and the first handshake says the server serves S1 now: the minted credential is superseded', async () => {
    const { posts, source } = harness({ bound_runtime_session: null, credential_owner: 'api_process' }, S1, IDLE_SERVING_SCAN);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1000);
    const session = store.getSnapshot().session;
    expect(session.credential_superseded).toBe(true);
    expect(session.boot_credential_owner).toBe('api_process');
    const markup = render(store);
    expect(stopButton(markup)).toContain('data-stop-takeover=""');
    expect(titleOf(stopButton(markup))).toMatch(/minted this tab's credential itself/);
    expect(buttonTag(markup, 'scan_scene')).toContain('aria-disabled="true"');
    expect(posts).toEqual([]);
    store.dispose();
  });

  it('served for S1 and answered by S1: the plain Stop, and Scan scene offered', async () => {
    const { posts, source } = harness({ bound_runtime_session: S1, credential_owner: 'runtime_session' }, S1, IDLE_SERVING_SCAN);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1000);
    const session = store.getSnapshot().session;
    expect(session.credential_superseded).toBe(false);
    expect(session.runtime_replaced).toBe(false);
    const markup = render(store);
    expect(stopButton(markup)).not.toContain('data-stop-reload');
    expect(buttonTag(markup, 'scan_scene')).toContain('aria-disabled="false"');
    expect(posts).toEqual([]);
    store.dispose();
  });
});
