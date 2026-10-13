// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// STOP ON A TAB WHOSE CREDENTIAL THE CONSOLE SERVER NO LONGER ACCEPTS (redesign r4 verify-r1 must_fix 1).
//
// The console API keeps one credential per runtime session and rotates it when the runtime under it is replaced; a tab
// still holding the old value is refused `credential_superseded` (HTTP 403) on POST /api/stop as on every command. Stop
// drawn as a working stop there is a control the server refuses while another display's run may move. The control stays
// in place, enabled and in its danger style, but it says what it is: "Reload to stop", with a title naming the console
// server's refusal and the reload, and a click that reloads the page instead of POSTing.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { textOf } from './demoScenarioPlay';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function stopTag(markup: string): string {
  return /<button[^>]*data-control="stop"[^>]*>[\s\S]*?<\/button>/.exec(markup)?.[0] ?? '(not drawn)';
}

function titleOf(tag: string): string {
  return /title="([^"]*)"/.exec(tag)?.[1] ?? '';
}

const RUNNING_ELSEWHERE = {
  ...runningSnapshot,
  data_mode: 'live',
  capabilities: [...runningSnapshot.capabilities.filter((item) => item.id !== 'stop'), { id: 'stop', state: 'enabled', reason_code: null, reason: null, depends_on: null }],
};

/** A console API over a runtime named `session()`, whose POSTs are answered by `post`. */
function harness(session: () => string, post: () => Response): { posts: string[]; source: HttpSource } {
  const posts: string[] = [];
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method !== 'GET') {
      posts.push(`${method} ${input}`);
      return post();
    }
    const current = session();
    if (input.startsWith('/api/hello')) {
      return json(200, {
        ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: current,
        credential_owner: 'runtime_session', bound_runtime_session: current, runtime: { reachable: true, session: current, contract_version: '1', data_mode: 'live' },
      });
    }
    if (input === '/api/snapshot') {
      return json(200, current === 'synthetic-session-0000' ? { ...idleSnapshot, runtime_session: current, data_mode: 'live' } : { ...RUNNING_ELSEWHERE, runtime_session: current });
    }
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  return { posts, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'stop-reload-test' }) };
}

describe('Stop on a tab whose runtime was replaced under it', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T05:00:00.000Z') }));
  afterEach(() => vi.useRealTimers());

  // Round 7 item 3: this case pinned "Reload to stop" before any press. A stale tab's Stop now takes over once on its press;
  // this harness serves no page at /, so its takeover cannot send, and the reload is what is left after that press.
  it('is the taking-over Stop before any press, and "Reload to stop", naming the refusal and the reload, after a press whose takeover could not send', async () => {
    let session = 'synthetic-session-0000';
    const { posts, source } = harness(() => session, () => json(403, { ok: false, reason_code: 'credential_superseded' }));
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(2000);
    session = 'synthetic-session-0001';
    await vi.advanceTimersByTimeAsync(6000);
    expect(store.getSnapshot().session.runtime_replaced).toBe(true);

    const before = stopTag(render(store));
    expect(before).toContain('aria-disabled="false"');
    expect(titleOf(before)).not.toContain('Ask the runtime to stop');
    expect(titleOf(before)).toMatch(/console server/);
    expect(titleOf(before)).toMatch(/as a reload does/);
    expect(textOf(before)).toBe('Stop');

    // A press is refused by the console server; this page was not served with a boot element, so it cannot take over.
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('refused');
    expect(ack.takeover ?? null).toBeNull();
    expect(posts).toEqual(['POST /api/stop']);
    await vi.advanceTimersByTimeAsync(1000);
    const after = stopTag(render(store));
    expect(after).toContain('aria-disabled="false"');
    expect(titleOf(after)).not.toContain('Ask the runtime to stop');
    expect(titleOf(after)).toMatch(/credential_superseded/);
    expect(textOf(after)).toBe('Reload to stop');
    store.dispose();
  });

  it('stays the plain Stop, with its title unchanged, while the runtime is the one the tab started against', async () => {
    const { posts, source } = harness(() => 'synthetic-session-0001', () => json(200, { ok: true, stop: { run_id: 'synthetic-run-0001' }, command: null, mission: null }));
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(6000);
    expect(store.getSnapshot().session.runtime_replaced).toBe(false);
    const tag = stopTag(render(store));
    expect(tag).toContain('aria-disabled="false"');
    expect(titleOf(tag)).toBe('Ask the runtime to stop the controller run and the mission. This is not an emergency stop.');
    expect(textOf(tag)).toBe('Stop');
    expect(posts).toEqual([]);
    store.dispose();
  });
});
