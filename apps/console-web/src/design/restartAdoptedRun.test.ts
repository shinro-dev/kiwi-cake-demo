// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AN OPEN TAB CONVERGES TO WHAT A FRESH TAB SHOWS AFTER A RUNTIME RESTART UNDER A MOVING RUN (redesign r4 round 7 item 4;
// verify-r6 closure M1, honesty M1, browser walk M1).
//
// A tab watches a run to PICK under runtime session S1. The daemon is restarted; the next daemon (S2) adopts the running
// controller, which goes on to place the block and end the run. The tab's Stages froze on "Pick In progress" (and its
// Scene on the run as it was) until a reload, beside a banner that said the record "keeps arriving and is being shown":
// after the replacement the selected run is drawn from its read-back fold, which was read ONCE, mid-run, and never again.
// Two shapes of the restart are pinned: the old event stream errors and reconnects (a SIGTERM, the honesty lens's r3d),
// and the old stream goes silent without an error (a kill -9 under the console API, the browser walk's M1). Every id,
// session, time and value is invented.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { parseBoot } from '../api/boot';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import type { ContractEvent } from '../contracts/envelope';
import { record } from '../model/stageFixtures';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { settle, textOf } from './demoScenarioPlay';

const S1 = 'synthetic-session-restart-0001';
const S2 = 'synthetic-session-restart-0002';
const RUN = 'synthetic-run-adopted-0001';
const T0 = Date.parse('2026-09-28T10:00:00.000Z');

type Restart = 'stream_errors' | 'stream_silent';

/** The runtime as the tab sees it through the console API: one retained log whose seq runs on across the restart. */
class Runtime {
  session = S1;
  log: ContractEvent[] = [];
  streams: Array<{ source: EventSourceLike; session: string; since: number; alive: boolean }> = [];
  historyReads = 0;
  /** When true every read is answered 503 runtime_unavailable, as the console API answers with no runtime under it. */
  down = false;

  append(kind: ContractEvent['kind'], payload: unknown, extra: Record<string, unknown> = {}): void {
    const seq = this.log.length + 1;
    const event = record(RUN, kind, payload as never, { seq, source_session: this.session, t_wall_utc: new Date(Date.now()).toISOString(), t_mono_ns: seq * 1_000_000, ...extra });
    this.log.push(event);
    for (const stream of this.streams) if (stream.alive && stream.session === this.session) stream.source.onmessage?.({ data: JSON.stringify(event) });
  }

  enter(to: string, from: string | null): void {
    this.append('state.transition', { machine: 'cycle', from_state: from, to_state: to });
  }

  finished(): ContractEvent | undefined {
    return this.log.find((event) => event.kind === 'run.finished');
  }

  snapshot(): Record<string, unknown> {
    const end = this.finished();
    const cursor = this.log.length;
    return {
      ...runningSnapshot,
      runtime_session: this.session,
      data_mode: 'live',
      seq_cursor: cursor,
      retained_from_seq: 1,
      capabilities: [...runningSnapshot.capabilities.filter((item) => item.id !== 'stop'), { id: 'stop', state: end === undefined ? 'enabled' : 'disabled', reason_code: end === undefined ? null : 'no_active_run', reason: null, depends_on: null }],
      active_run: end === undefined ? { run_id: RUN, command_id: `cmd-${RUN}`, command_type: 'start_cycle', status: 'running', state: 'ALIGN', started_seq: 1 } : null,
      last_outcome:
        end === undefined
          ? null
          : {
              run_id: RUN,
              finished_seq: end.seq,
              result: { outcome: 'placed_new', exit_status: 0, predicates: { object_secured: true, placed: true, clearance_target_met: true, base_returned: true }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0 },
            },
    };
  }

  fetchImpl(): FetchLike {
    return async (input, init) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method !== 'GET') return json(599, { ok: false, reason_code: 'this_test_sends_no_command' });
      if (this.down) return json(503, { ok: false, reason_code: 'runtime_unavailable' });
      if (input.startsWith('/api/hello')) {
        return json(200, {
          ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: this.session,
          credential_owner: 'runtime_session', bound_runtime_session: this.session, runtime: { reachable: true, session: this.session, contract_version: '1', data_mode: 'live' },
        });
      }
      if (input === '/api/snapshot') return json(200, this.snapshot());
      if (input === '/api/history') {
        const end = this.finished();
        return json(200, {
          ok: true,
          runs: end === undefined ? [] : [{ run_id: RUN, command_id: `cmd-${RUN}`, command_type: 'start_cycle', started_wall_utc: this.log[0]?.t_wall_utc, finished_wall_utc: end.t_wall_utc, outcome: 'placed_new', exit_status: 0, data_mode: 'live' }],
        });
      }
      if (input === `/api/history/${RUN}/events`) {
        this.historyReads += 1;
        return json(200, { ok: true, run_id: RUN, events: this.log.filter((event) => event.run_id === RUN) });
      }
      if (input === '/api/mission') return json(404, { ok: false, reason_code: 'no_mission_recorded' });
      return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
    };
  }

  eventSourceFactory(): (url: string) => EventSourceLike {
    return (url) => {
      // The telemetry stream shares the factory; it carries no record and is not this runtime's log.
      if (!url.includes('/events?')) return { onmessage: null, onerror: null, close: () => undefined };
      const since = Number(new URL(url, 'http://console.invalid').searchParams.get('since') ?? '0');
      const source: EventSourceLike = { onmessage: null, onerror: null, close: () => (entry.alive = false) };
      const entry = { source, session: this.session, since, alive: true };
      this.streams.push(entry);
      // The backlog after the cursor, as the runtime serves it on subscribe.
      setTimeout(() => {
        if (!entry.alive) return;
        for (const event of this.log) if (event.seq > since) source.onmessage?.({ data: JSON.stringify(event) });
      }, 0);
      return source;
    };
  }

  restart(shape: Restart): void {
    for (const stream of this.streams) {
      if (!stream.alive) continue;
      if (shape === 'stream_errors') {
        stream.alive = false;
        stream.source.onerror?.(new Event('error'));
      }
      // stream_silent: the old stream stays open and delivers nothing more, and never errors.
    }
    this.session = S2;
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function tab(runtime: Runtime, servedFor: string, name: string): ConsoleStore {
  const boot = parseBoot({
    contract_version: '1', api_base: '/api', api_session: 'api-0123456789ab', credential: `credential-of-${servedFor}`, credential_header: 'X-Console-Credential',
    request_header: 'X-Console-Request', bound_runtime_session: servedFor, credential_owner: 'runtime_session',
  });
  return new ConsoleStore(new HttpSource({ fetchImpl: runtime.fetchImpl(), eventSourceFactory: runtime.eventSourceFactory(), boot, sessionId: name, reconnectDelayMs: 1000 }), { storage: null });
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function region(markup: string, name: string): string {
  const start = markup.indexOf(`data-region="${name}"`);
  if (start < 0) return '(not drawn)';
  const open = markup.lastIndexOf('<section', start);
  const close = markup.indexOf('</section>', start);
  return markup.slice(open, close);
}

function stageStatuses(markup: string): string[] {
  return [...region(markup, 'stages').matchAll(/data-stage-status="([^"]*)"/g)].map((match) => match[1] as string);
}

async function play(shape: Restart): Promise<{ stale: ConsoleStore; runtime: Runtime }> {
  vi.useFakeTimers({ now: new Date(T0) });
  const runtime = new Runtime();
  runtime.append('run.started', { command_id: `cmd-${RUN}`, command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: false });
  const stale = tab(runtime, S1, 'stale-tab');
  await stale.start();
  await settle(stale);
  for (const [to, from] of [['SEARCH', null], ['PREGRASP', 'SEARCH'], ['ALIGN', 'PREGRASP'], ['GRASP_CLOSE', 'ALIGN']] as Array<[string, string | null]>) {
    await vi.advanceTimersByTimeAsync(1000);
    runtime.append('state.transition', { machine: 'cycle', from_state: from, to_state: to });
  }
  await settle(stale);
  expect(stageStatuses(render(stale))).toContain('active');

  // The daemon is replaced; the next one adopts the controller, which carries on.
  await vi.advanceTimersByTimeAsync(500);
  runtime.restart(shape);
  await vi.advanceTimersByTimeAsync(6000);
  await settle(stale);
  expect(stale.getSnapshot().session.runtime_replaced).toBe(true);
  for (const [to, from] of [['PLACEMENT_PLAN', 'GRASP_CLOSE'], ['BASE_ADVANCE', 'PLACEMENT_PLAN'], ['RELEASE_ADMISSION', 'BASE_ADVANCE'], ['PLACED', 'RELEASE_ADMISSION'], ['BASE_RETURN', 'PLACED'], ['COMPLETE', 'BASE_RETURN']] as Array<[string, string]>) {
    await vi.advanceTimersByTimeAsync(1500);
    runtime.enter(to, from);
    await settle(stale);
  }
  runtime.append('run.finished', {
    outcome: 'placed_new', exit_status: 0, predicates: { object_secured: true, placed: true, clearance_target_met: true, base_returned: true },
    automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, reason_code: null, phase: null, failure: null,
  });
  // One poll and a little: the tab's own reads only.
  for (let step = 0; step < 7; step += 1) {
    await vi.advanceTimersByTimeAsync(1000);
    await settle(stale);
  }
  return { stale, runtime };
}

describe('a tab open across a runtime restart that adopted the running run', () => {
  afterEach(() => vi.useRealTimers());

  for (const shape of ['stream_errors', 'stream_silent'] as Restart[]) {
    it(`${shape}: within one poll of the run's end no Stage is In progress, and the Stages and the Scene equal a fresh tab's`, async () => {
      const { stale, runtime } = await play(shape);
      const staleMarkup = render(stale);
      expect(stageStatuses(staleMarkup)).not.toContain('active');
      // No row pairs "Not yet" with a time it was entered.
      expect(textOf(region(staleMarkup, 'stages'))).not.toMatch(/Not yet entered/);

      const fresh = tab(runtime, S2, 'fresh-tab');
      await fresh.start();
      await settle(fresh);
      await vi.advanceTimersByTimeAsync(1000);
      await settle(fresh);
      const freshMarkup = render(fresh);
      expect(textOf(region(staleMarkup, 'stages'))).toBe(textOf(region(freshMarkup, 'stages')));
      expect(stageStatuses(staleMarkup)).toEqual(stageStatuses(freshMarkup));
      expect(textOf(region(staleMarkup, 'scene'))).toBe(textOf(region(freshMarkup, 'scene')));
      stale.dispose();
      fresh.dispose();
    }, 60_000);
  }

  it('does not say the record keeps arriving while the runtime does not answer this display', async () => {
    const { stale, runtime } = await play('stream_silent');
    expect(textOf(render(stale))).toContain('The record below keeps arriving and is being shown');
    runtime.down = true;
    await vi.advanceTimersByTimeAsync(6000);
    const words = textOf(render(stale));
    expect(stale.getSnapshot().sourceError).not.toBeNull();
    expect(words).not.toContain('keeps arriving');
    expect(words).toContain('The record below is the last this display read');
    stale.dispose();
  }, 60_000);

  it('stream_silent: reopens its event stream under the new session, from the cursor of the snapshot that named it, and closes the silent one', async () => {
    const { stale, runtime } = await play('stream_silent');
    const alive = runtime.streams.filter((stream) => stream.alive);
    expect(alive).toHaveLength(1);
    expect(alive[0]?.session).toBe(S2);
    // The silent stream of S1 was closed by the tab, not left open beside the new one.
    expect(runtime.streams.filter((stream) => stream.session === S1).every((stream) => !stream.alive)).toBe(true);
    stale.dispose();
  }, 60_000);
});
