// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AFTER A STANDALONE RUN ENDS NOTHING SAYS A RUN IS ACTIVE, AND THE DISPLAY'S OWN WORDS FOR THE MOMENT LAST ABOUT A SECOND,
// NOT A POLL (redesign r4 should_fix honesty S1, carried since the first verdict).
//
// After a Scan scene, an Observe or a Return arm ends, the runtime's snapshot still names the run until it lets go of it
// (`demo_runtime/supervisor.py`: the run.finished emit, then `_release`). The default screen says that in its own voice
// since refix round 3 (deviation 69), and reads the snapshot once more a second after the end. Two gaps were left: the
// classic screen's action card printed the runtime's stale reason as current ("What stands in the way of a run: a run is
// already in progress") beside the run's end, and a snapshot that still named the run at that one re-read was left to the
// next 5 s poll. The classic card now says it as the default screen does, and the snapshot is read again each second
// while it names a run whose end the record holds, until the poll takes over.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import type { Capability, Command } from '@kiwi/demo-contracts';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { SyntheticSource } from '../api/synthetic';
import type { ContractEvent } from '../contracts/envelope';
import { finished, record } from '../model/stageFixtures';
import type { SyntheticScenario } from '../panels/sourceChoice';
import { scanSceneCommand } from '../panels/Actions';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { Screen } from './Screen';
import { readScreen, textOf } from './demoScenarioPlay';

function render(store: ConsoleStore, screen: 'demo' | 'classic'): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(screen === 'demo' ? DemoScreen : Screen)));
}

/** The runtime's words for a run in the slot, printed as current. */
const RUN_IN_SLOT = /a run is already in progress|a run is active/i;

function stale(markup: string): string | null {
  const titles = [...markup.matchAll(/title="([^"]*)"/g)].map((match) => match[1] ?? '').filter((title) => RUN_IN_SLOT.test(title));
  const words = RUN_IN_SLOT.exec(textOf(markup));
  return words === null && titles.length === 0 ? null : (words?.[0] ?? `title: ${titles[0] ?? ''}`);
}

const CASES: { scenario: SyntheticScenario; label: string; command: Command }[] = [
  { scenario: 'scene-scan', label: 'Scan scene', command: scanSceneCommand() },
  { scenario: 'observe-landmarks-only', label: 'Observe', command: { type: 'observe', args: { holding: false } } as Command },
  { scenario: 'far-block-success', label: 'Return arm', command: { type: 'return_arm', args: { plan_only: false } } as Command },
];

describe('a standalone run ends, in the page', () => {
  afterEach(() => vi.useRealTimers());

  for (const item of CASES) {
    it(`${item.label}: once the record holds the run's end, neither screen says a run is in the slot, and the display's words last about a second`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-28T05:00:00Z') });
      const store = new ConsoleStore(new SyntheticSource({ scenario: item.scenario }), { storage: null });
      await store.start();
      await vi.advanceTimersByTimeAsync(6000);
      const track = await store.submit(item.command);
      const runId = track.result?.run_id ?? null;
      expect(runId).not.toBeNull();
      const bad: string[] = [];
      const notIdle: number[] = [];
      let endedAt: number | null = null;
      for (let k = 0; k < 1200; k += 1) {
        await vi.advanceTimersByTimeAsync(50);
        const view = store.getSnapshot().view;
        const ended = view.outcome.run_id === runId && view.outcome.finished_seq !== null;
        if (!ended) continue;
        endedAt ??= k;
        for (const screen of ['demo', 'classic'] as const) {
          const markup = render(store, screen);
          const found = stale(markup);
          if (found !== null) bad.push(`${screen} +${String((k - endedAt) * 50)} ms after the end: "${found}"`);
          if (screen === 'demo' && readScreen(markup, store).readinessSentence.includes('has not yet reported itself idle')) notIdle.push(k);
        }
        if (k - endedAt > 120) break;
      }
      expect(endedAt).not.toBeNull();
      expect(bad).toEqual([]);
      // The display's own words for the moment the runtime has not let go of the run: a second or so, never a poll.
      expect(notIdle.length * 50).toBeLessThanOrEqual(1100);
      store.dispose();
    }, 120_000);
  }
});

/* ---- the runtime path: a snapshot that keeps naming the ended run for more than one re-read ------------------------ */

const SESSION = 'synthetic-session-0000';
const HELLO = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: SESSION,
  credential_owner: 'runtime_session', bound_runtime_session: SESSION,
  runtime: { reachable: true, session: SESSION, contract_version: '1', data_mode: 'live' },
};

function capabilities(active: boolean): Capability[] {
  const off = (id: string): Capability => ({ id, state: 'blocked', reason_code: 'run_active', reason: 'a run is active: only stop is accepted', dependency: 'owner', planned: false });
  const on = (id: string): Capability => ({ id, state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false });
  return active ? [off('start_cycle'), off('scan_scene'), on('stop')] : [on('start_cycle'), on('scan_scene')];
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

/** A runtime whose snapshot names run `scan-1` for `staleReads` reads after the stream delivered its run.finished. */
function runtime(staleReads: number): { reads: string[]; emit: (event: ContractEvent) => void; source: HttpSource; state: { ended: boolean; cursor: number } } {
  const reads: string[] = [];
  const state = { ended: false, stale: staleReads, cursor: 0 };
  let stream: EventSourceLike | null = null;
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    reads.push(`${method} ${input}`);
    if (input.startsWith('/api/hello')) return json(200, HELLO);
    if (input === '/api/snapshot') {
      const active = !state.ended || state.stale > 0;
      if (state.ended && state.stale > 0) state.stale -= 1;
      const activeRun = active ? { run_id: 'scan-1', command_id: 'cmd-scan-1', command_type: 'scan_scene', status: 'running', state: 'SCAN', started_seq: 1 } : null;
      return json(200, { ...idleSnapshot, runtime_session: SESSION, data_mode: 'live', seq_cursor: state.cursor, active_run: activeRun, capabilities: capabilities(active) });
    }
    if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (url: string): EventSourceLike => {
    const opened: EventSourceLike = { onmessage: null, onerror: null, close: () => undefined };
    if (url.includes('/events')) stream = opened;
    return opened;
  };
  const emit = (event: ContractEvent): void => {
    state.cursor = event.seq;
    stream?.onmessage?.({ data: JSON.stringify(event) });
  };
  return { reads, emit, state, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'standalone-run-end-test' }) };
}

function snapshotReadsSince(reads: readonly string[], from: number): number {
  return reads.slice(from).filter((read) => read === 'GET /api/snapshot').length;
}

describe('the store over the runtime: a run.finished the snapshot is slow to follow', () => {
  afterEach(() => vi.useRealTimers());

  it('reads the snapshot again each second while it names the ended run, and stops once it does not', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T05:00:00.000Z') });
    const { reads, emit, state, source } = runtime(3);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(100);
    expect(store.getSnapshot().view.active_run?.run_id).toBe('scan-1');
    const started = { ...record('scan-1', 'run.started', { command_id: 'cmd-scan-1', command_type: 'scan_scene', data_mode: 'live', plan_only: false, holding: null } as never), seq: 1 } as ContractEvent;
    emit(started);
    await vi.advanceTimersByTimeAsync(100);
    const before = reads.length;
    state.ended = true;
    emit({ ...finished('scan-1', 'unknown' as 'failed', null), seq: 2 } as ContractEvent);
    // The read on the end and the re-reads a second apart: the first three still name the run.
    await vi.advanceTimersByTimeAsync(50);
    expect(snapshotReadsSince(reads, before)).toBe(1);
    const words = readScreen(render(store, 'demo'), store);
    expect(words.readinessSentence).toBe('The run has ended by its own record; the runtime has not yet reported itself idle.');
    expect(stale(render(store, 'classic'))).toBeNull();
    await vi.advanceTimersByTimeAsync(1000);
    expect(snapshotReadsSince(reads, before)).toBe(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(snapshotReadsSince(reads, before)).toBe(3);
    // The fourth, 3 s after the end and before the 5 s poll, finds the runtime idle; nothing is read again after it.
    await vi.advanceTimersByTimeAsync(1000);
    expect(snapshotReadsSince(reads, before)).toBe(4);
    expect(store.getSnapshot().view.active_run).toBeNull();
    expect(readScreen(render(store, 'demo'), store).readinessSentence).not.toMatch(/has not yet reported itself idle/);
    await vi.advanceTimersByTimeAsync(1500);
    expect(snapshotReadsSince(reads, before)).toBe(4);
    store.dispose();
  });
});
