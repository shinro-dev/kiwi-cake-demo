// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A STALE TAB'S FIRST STOP PRESS STOPS THE RUN (redesign r4 verify-r4 must_fix 4).
//
// The console API rotates its credential when any client makes it follow a new runtime session (a second display's page
// load, its handshake or its poll), and a tab learns of the new session only from its own reads, on the 5 s poll. In that
// window the tab drew the plain Stop, and a press was POSTed and refused `credential_superseded` before it reached the
// runtime, in the case where the new runtime may have adopted a controller that is still moving (the judge's E2b).
//
// What "Reload to stop" asks a human to do is read the page the console server serves now, which carries the credential
// it accepts, and press Stop there. On a `credential_superseded` refusal the tab now does that itself, once: it reads the
// page (GET /, same origin) for the credential in its boot element and sends Stop ONE more time with it. No server
// change, no new authority (it is the reload's own path), the credential is used for that one Stop and kept nowhere, and
// nothing is sent a third time. The fake below is the console server's guard as `console_api/security.py` states it: one
// accepted credential, the ones it served before refused `credential_superseded`, any other `credential_invalid`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { bootFromHtml, parseBoot } from '../api/boot';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { TopBar } from './TopBar';
import type { TopBarProps } from './TopBar';
import { textOf } from './demoScenarioPlay';

const S1 = 'synthetic-session-0001';
const S2 = 'synthetic-session-0002';
const RUN = 'synthetic-run-0001';
const CREDENTIAL_HEADER = 'X-Console-Credential';

const RUNNING = {
  ...runningSnapshot,
  data_mode: 'live',
  capabilities: [...runningSnapshot.capabilities.filter((item) => item.id !== 'stop'), { id: 'stop', state: 'enabled', reason_code: null, reason: null, depends_on: null }],
};

function hello(session: string): Record<string, unknown> {
  return {
    ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: session,
    credential_owner: 'runtime_session', bound_runtime_session: session, runtime: { reachable: true, session, contract_version: '1', data_mode: 'live' },
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

/** The page the console API serves, with its boot element as `console_api/web.py` renders it ('<' escaped). */
function servedPage(credential: string, session: string): string {
  const boot = {
    contract_version: '1', api_base: '/api', api_session: 'api-0123456789ab', credential, credential_header: CREDENTIAL_HEADER,
    request_header: 'X-Console-Request', bound_runtime_session: session, credential_owner: 'runtime_session',
  };
  const payload = JSON.stringify(boot).replace(/</g, '\\u003c');
  return `<!doctype html><html><head><meta charset="utf-8"><script type="application/json" id="kc-console-boot">${payload}</script><script type="module" src="/assets/index-AAAAAAAA.js"></script></head><body><div id="root"></div></body></html>`;
}

interface Server {
  /** The credential the guard accepts now, and the session it belongs to. */
  accepted: string;
  session: string;
  /** Values the guard served before and has superseded. */
  superseded: string[];
  /** What GET / answers: the page for the accepted credential, or a refusal. */
  page: 'served' | 'not_attached' | 'same_as_tab';
  /** When set, the guard rotates to this value right after it serves the page (another client got there first). */
  rotateAfterPage: string | null;
}

function harness(server: Server): { source: HttpSource; posts: Array<{ path: string; credential: string | null }>; pages: number; stopsReached: () => number } {
  const posts: Array<{ path: string; credential: string | null }> = [];
  let pages = 0;
  let reached = 0;
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    if (method === 'POST') {
      const credential = ((init?.headers ?? {}) as Record<string, string>)[CREDENTIAL_HEADER] ?? null;
      posts.push({ path: input, credential });
      if (credential !== server.accepted) {
        return json(403, { ok: false, reason_code: credential !== null && server.superseded.includes(credential) ? 'credential_superseded' : 'credential_invalid' });
      }
      if (input !== '/api/stop') return json(599, { ok: false, reason_code: 'this_test_sends_only_stop' });
      reached += 1;
      const body = JSON.parse(String(init?.body ?? '{}')) as { command_id?: string };
      return json(200, {
        ok: true, reason_code: null,
        command: { command_id: body.command_id ?? 'unknown', status: 'finished', reason_code: null, run_id: RUN },
        stop: { requested_mono_ns: null, run_id: RUN },
        mission: null,
      });
    }
    if (input === '/') {
      pages += 1;
      if (server.page === 'not_attached') return json(404, { ok: false, reason_code: 'web_build_not_attached' });
      const html = servedPage(server.page === 'same_as_tab' ? 'credential-of-s1' : server.accepted, server.session);
      if (server.rotateAfterPage !== null) {
        server.superseded.push(server.accepted);
        server.accepted = server.rotateAfterPage;
        server.rotateAfterPage = null;
      }
      return new Response(html, { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } });
    }
    if (input.startsWith('/api/hello')) return json(200, hello(server.session));
    if (input === '/api/snapshot') return json(200, { ...RUNNING, runtime_session: server.session });
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  const boot = parseBoot({
    contract_version: '1', api_base: '/api', api_session: 'api-0123456789ab', credential: 'credential-of-s1', credential_header: CREDENTIAL_HEADER,
    request_header: 'X-Console-Request', bound_runtime_session: S1, credential_owner: 'runtime_session',
  });
  return {
    source: new HttpSource({ fetchImpl, eventSourceFactory, boot, sessionId: 'stale-tab-stop-test' }),
    posts,
    get pages() {
      return pages;
    },
    stopsReached: () => reached,
  };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function stopButton(markup: string): string {
  return /<button[^>]*data-control="stop"[^>]*>[\s\S]*?<\/button>/.exec(markup)?.[0] ?? '(not drawn)';
}

/** A tab served and answered by S1; then another client makes the API follow S2 and rotate. 4 s later, before this tab's poll, Stop is pressed. */
async function staleTab(server: Server): Promise<{ store: ConsoleStore; h: ReturnType<typeof harness> }> {
  const h = harness(server);
  const store = new ConsoleStore(h.source, { storage: null });
  await store.start();
  await vi.advanceTimersByTimeAsync(500);
  server.superseded.push(server.accepted);
  server.accepted = 'credential-of-s2';
  server.session = S2;
  await vi.advanceTimersByTimeAsync(4000);
  return { store, h };
}

function freshServer(extra: Partial<Server> = {}): Server {
  return { accepted: 'credential-of-s1', session: S1, superseded: [], page: 'served', rotateAfterPage: null, ...extra };
}

function stopReceiptText(store: ConsoleStore): string {
  return store.getSnapshot().presses.find((press) => press.control === 'stop')?.text ?? '';
}

describe('a stale tab pressing Stop inside the window before its poll learns of the new session', () => {
  afterEach(() => vi.useRealTimers());

  it('stops the run on the first press: refused credential_superseded, then the page is read once and Stop is sent once more with its credential', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const server = freshServer();
    const { store, h } = await staleTab(server);
    // The window the judge measured: the tab does not know yet, and draws the plain Stop.
    const before = stopButton(render(store));
    expect(textOf(before)).toBe('Stop');
    expect(before).not.toContain('data-stop-reload');

    const ack = await store.requestStop();
    expect(ack.outcome).toBe('taken');
    expect(ack.run_id).toBe(RUN);
    expect(h.stopsReached()).toBe(1);
    expect(h.posts.map((post) => post.path)).toEqual(['/api/stop', '/api/stop']);
    expect(h.posts.map((post) => post.credential)).toEqual(['credential-of-s1', 'credential-of-s2']);
    expect(h.pages).toBe(1);
    const receipt = stopReceiptText(store);
    expect(receipt).toContain("The console server refused this tab's credential (credential_superseded)");
    expect(receipt).toContain('This display read the page the console server serves now, as a reload does, and sent Stop once more with the credential in it');
    expect(receipt).toContain(`Stop taken: the runtime signalled the controller run ${RUN} to stop`);
    // This tab's own credential is still the superseded one: what it holds is said, and nothing else it offers is sent with the new one.
    await vi.advanceTimersByTimeAsync(100);
    expect(store.getSnapshot().session.credential_superseded).toBe(true);
    expect(h.posts).toHaveLength(2);
    store.dispose();
  }, 60_000);

  it('after the takeover no region says the tab cannot obtain the new credential beside the receipt that says it used it', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const { store } = await staleTab(freshServer());
    await store.requestStop();
    await vi.advanceTimersByTimeAsync(100);
    const words = textOf(render(store));
    expect(words).toContain('sent Stop once more with the credential in it');
    expect(words).not.toMatch(/cannot obtain the new one/);
    expect(words).toContain('no handshake gives this tab the new one; a reload does, for every control');
    store.dispose();
  }, 60_000);

  it('sends no second Stop when the page cannot be read, and says so', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const { store, h } = await staleTab(freshServer({ page: 'not_attached' }));
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('refused');
    expect(h.posts).toHaveLength(1);
    expect(h.pages).toBe(1);
    expect(h.stopsReached()).toBe(0);
    const receipt = stopReceiptText(store);
    expect(receipt).toContain('Stop refused by the console server: credential_superseded. Nothing reached the runtime');
    expect(receipt).toContain('could not get a new credential from the page the console server serves now (web_build_not_attached), so no second Stop was sent');
    store.dispose();
  }, 60_000);

  it('sends Stop at most twice for one press: a second refusal is reported, never retried', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const server = freshServer();
    const { store, h } = await staleTab(server);
    server.rotateAfterPage = 'credential-of-s3';
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('refused');
    expect(ack.reason).toBe('credential_superseded');
    expect(h.posts).toHaveLength(2);
    expect(h.pages).toBe(1);
    expect(h.stopsReached()).toBe(0);
    await vi.advanceTimersByTimeAsync(6000);
    expect(h.posts).toHaveLength(2);
    expect(stopReceiptText(store)).toContain('refused it again');
    store.dispose();
  }, 60_000);

  it('sends no second Stop when the page it reads carries the credential this tab already holds', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const { store, h } = await staleTab(freshServer({ page: 'same_as_tab' }));
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('refused');
    expect(h.posts).toHaveLength(1);
    expect(h.pages).toBe(1);
    store.dispose();
  }, 60_000);

  it('takes over only on credential_superseded: a credential the server never served is refused once and the page is not read', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const server = freshServer();
    const h = harness(server);
    const store = new ConsoleStore(h.source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(500);
    server.accepted = 'credential-of-another-api';
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('refused');
    expect(ack.reason).toBe('credential_invalid');
    expect(h.posts).toHaveLength(1);
    expect(h.pages).toBe(0);
    store.dispose();
  }, 60_000);
});

/** Find the one element whose props carry `data-control="stop"` in a rendered-by-call element tree. */
function findStop(node: unknown): { onClick?: () => void; children?: unknown } | null {
  if (node === null || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const hit = findStop(item);
      if (hit !== null) return hit;
    }
    return null;
  }
  const props = (node as { props?: Record<string, unknown> }).props;
  if (props === undefined) return null;
  if (props['data-control'] === 'stop') return props as { onClick?: () => void };
  return findStop(props['children']);
}

describe('a tab that already knows its credential is superseded (round 7 item 3)', () => {
  afterEach(() => vi.useRealTimers());

  /** The stale tab past its poll: its own reads have told it the server serves S2 now. */
  async function knowingTab(server: Server): Promise<{ store: ConsoleStore; h: ReturnType<typeof harness> }> {
    const tab = await staleTab(server);
    await vi.advanceTimersByTimeAsync(6000);
    // The tab's own poll told it: the handshake names S2 as the session the server serves, not the S1 it was served for.
    expect(tab.store.getSnapshot().session.runtime_replaced).toBe(true);
    return tab;
  }

  it('draws the plain Stop, whose title says what a press does and no longer that a Stop from this tab would be refused', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const { store } = await knowingTab(freshServer());
    const button = stopButton(render(store));
    expect(textOf(button)).toBe('Stop');
    expect(button).toContain('data-stop-takeover');
    expect(button).not.toContain('data-stop-reload');
    expect(button).toContain('aria-disabled="false"');
    const title = /title="([^"]*)"/.exec(button)?.[1] ?? '';
    expect(title).not.toMatch(/would be refused|is refused/);
    expect(title).toContain('reads the page the console server serves now, as a reload does, and sends Stop once more');
    store.dispose();
  }, 60_000);

  it('stops the run on its first press, through the one-time takeover, and keeps the plain Stop afterwards', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const { store, h } = await knowingTab(freshServer());
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('taken');
    expect(h.stopsReached()).toBe(1);
    expect(h.posts.map((post) => post.credential)).toEqual(['credential-of-s1', 'credential-of-s2']);
    await vi.advanceTimersByTimeAsync(100);
    const after = stopButton(render(store));
    expect(textOf(after)).toBe('Stop');
    expect(after).not.toContain('data-stop-reload');
    store.dispose();
  }, 60_000);

  it('falls back to "Reload to stop" only after a press whose takeover could not send, and names why', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T06:58:40.000Z') });
    const { store, h } = await knowingTab(freshServer({ page: 'not_attached' }));
    expect(textOf(stopButton(render(store)))).toBe('Stop');
    const ack = await store.requestStop();
    expect(ack.takeover?.sent).toBe(false);
    expect(h.stopsReached()).toBe(0);
    await vi.advanceTimersByTimeAsync(100);
    const after = stopButton(render(store));
    expect(textOf(after)).toBe('Reload to stop');
    expect(after).toContain('data-stop-reload');
    expect(after).toContain('web_build_not_attached');
    store.dispose();
  }, 60_000);

  it("routes the stale Stop's press to Stop and only the fallback's to the reload", () => {
    const onStop = vi.fn();
    const onReload = vi.fn();
    const base = {
      mode: { id: 'live', word: 'Live', title: '' },
      robot: { label: 'robot', connection: 'answering', tone: 'ok', notReadSince: null },
      motion: null,
      readiness: { verdict: { tone: 'ok', words: 'Ready' }, sentence: null, startPrimary: false, checked: [], unchecked: [] },
      start: { label: 'Start', off: 'not under test', busy: false },
      onStart: () => undefined,
      onStop,
      onReload,
      onDiagnostics: () => undefined,
    } as unknown as TopBarProps;
    findStop(TopBar({ ...base, stop: { off: null, busy: false, stale: { reload: false, title: 'stale' } } }))?.onClick?.();
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onReload).not.toHaveBeenCalled();
    findStop(TopBar({ ...base, stop: { off: null, busy: false, stale: { reload: true, title: 'reload' } } }))?.onClick?.();
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});

describe('the boot element in a served page', () => {
  it('reads the element the console API renders, with its escaped values', () => {
    const boot = bootFromHtml(servedPage('credential-of-s2', S2));
    expect(boot?.credentials?.credential).toBe('credential-of-s2');
    expect(boot?.credentials?.credential_header).toBe(CREDENTIAL_HEADER);
    expect(boot?.served_credential).toEqual({ runtime_session: S2, owner: 'runtime_session' });
    const escaped = bootFromHtml(servedPage('a<b', S2));
    expect(escaped?.credentials?.credential).toBe('a<b');
  });

  it('is null for a page with no element, an unclosed element or one that is not JSON', () => {
    expect(bootFromHtml('<!doctype html><html><head></head><body></body></html>')).toBeNull();
    expect(bootFromHtml('<script type="application/json" id="kc-console-boot">{"credential":"x"')).toBeNull();
    expect(bootFromHtml('<script type="application/json" id="kc-console-boot">not json</script>')).toBeNull();
  });
});
