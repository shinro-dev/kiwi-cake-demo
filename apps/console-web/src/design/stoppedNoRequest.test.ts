// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A MISSION THE RUNTIME ENDED STOPPED WITH NO STOP ASKED FOR IS NEVER SAID TO HAVE STOPPED ON REQUEST (redesign r4 verify-r4
// must_fix 1).
//
// The runtime ends the mission it carries when it shuts down (`demo_runtime/mission_coordinator.py` close: "It is not
// written down as an operator's stop request: nobody asked for one, the runtime is ending"). Its record then reads
// `state STOPPED`, `stop_requested false`, `stop_requested_wall_utc null`, `detail "stopped while carrying a target"`.
// The card, the timeline and History all drew "Mission stopped on request", a cause the record contradicts, and the
// record's detail was on no region of the main screen. The record below has the fields the honesty lens read from its
// synthetic runtime after a SIGTERM under a held mission, with this file's own ids.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { Drawer } from './Drawers';
import { element, settle, textOf } from './demoScenarioPlay';

const SESSION = 'synthetic-session-0000';
const HELLO = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: SESSION,
  credential_owner: 'runtime_session', bound_runtime_session: SESSION,
  runtime: { reachable: true, session: SESSION, contract_version: '1', data_mode: 'synthetic' },
};

/** The record as the runtime serves it after it ended the held mission at its own shutdown. */
function endedAtShutdown(): Record<string, unknown> {
  return {
    mission_record_version: 1,
    mission_id: 'mission-0001',
    state: 'STOPPED',
    data_mode: 'synthetic',
    performs_motion: false,
    execution_label: 'recording-rehearsal',
    started_wall_utc: '2026-09-28T10:10:07.078Z',
    selected_target: 'cube',
    waiting_reason: 'capability_unavailable',
    detail: 'stopped while carrying a target',
    stop_requested: false,
    stop_requested_wall_utc: null,
    records_after_end: 0,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'STOPPED', runs: [], binding: null, evidence: null, attempts: [], retries: [], park_runs: [] }],
    activations: [],
    hold: null,
    next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

async function opened(mission: Record<string, unknown>): Promise<ConsoleStore> {
  const fetchImpl: FetchLike = async (input, init) => {
    if ((init?.method ?? 'GET').toUpperCase() !== 'GET') return json(599, { ok: false, reason_code: 'this_test_sends_no_command' });
    if (input.startsWith('/api/hello')) return json(200, HELLO);
    if (input === '/api/snapshot') return json(200, { ...idleSnapshot, runtime_session: SESSION, data_mode: 'synthetic', active_run: null });
    if (input === '/api/mission') return json(200, mission);
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'stopped-no-request-test' }), { storage: null });
  await store.start();
  await vi.advanceTimersByTimeAsync(500);
  await settle(store);
  return store;
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function history(store: ConsoleStore): string {
  const snapshot = store.getSnapshot();
  return textOf(
    renderToStaticMarkup(
      createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'history', snapshot, store, mode: 'synthetic', onSelect: () => undefined, onClose: () => undefined })),
    ),
  );
}

function region(markup: string, name: string): string {
  const found = element(markup, `data-region="${name}"`);
  return found === null ? '' : textOf(found);
}

describe('a mission the runtime ended STOPPED at its own shutdown, read by a page opened after the restart', () => {
  afterEach(() => vi.useRealTimers());

  it('says no "on request" on the card, the timeline or History, and puts the record\'s detail on the card in the runtime\'s voice', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T10:12:00.000Z') });
    const store = await opened(endedAtShutdown());
    const markup = render(store);
    const card = region(markup, 'mission');
    const timeline = region(markup, 'timeline');
    const drawn = history(store);
    expect(card).not.toBe('');
    expect(timeline).not.toBe('');
    expect(card).not.toMatch(/on request/i);
    expect(timeline).not.toMatch(/on request/i);
    expect(drawn).not.toMatch(/on request/i);
    expect(/data-verdict="([^"]*)"/.exec(element(markup, 'data-outcome-card=') ?? '')?.[1]).toBe('stopped_no_request');
    expect(card).toContain('Mission stopped with no stop request on its record');
    expect(timeline).toContain('Mission stopped with no stop request on its record');
    expect(drawn).toContain('Mission stopped with no stop request on its record');
    expect(card).toContain("The runtime's detail: stopped while carrying a target");
    store.dispose();
  });

  it('keeps "Mission stopped on request" for the same record when its stop was asked for, with the detail beside it', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T10:12:00.000Z') });
    const store = await opened({ ...endedAtShutdown(), stop_requested: true, stop_requested_wall_utc: '2026-09-28T10:11:00.000Z' });
    const markup = render(store);
    const card = region(markup, 'mission');
    expect(/data-verdict="([^"]*)"/.exec(element(markup, 'data-outcome-card=') ?? '')?.[1]).toBe('stopped_on_request');
    expect(card).toContain('Mission stopped on request');
    expect(region(markup, 'timeline')).toContain('Mission stopped on request');
    expect(card).toContain("The runtime's detail: stopped while carrying a target");
    store.dispose();
  });
});
