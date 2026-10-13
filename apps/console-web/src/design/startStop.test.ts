// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// START IS THE MISSION START, AND STOP SAYS WHAT IT STOPPED.
//
// The operator's requirements, pinned here:
//
//   "The operator-facing Start must address the canonical mission start path. Keep it disabled in LIVE mode until the
//   real execution adapter is ready." So a press sends POST /api/mission/start and never start_cycle, and on a runtime
//   that serves no mission the control is off by the native attribute AND aria-disabled, with the runtime's own reason
//   on the screen beside it rather than only in a tooltip.
//
//   "Stop must cancel the mission and stop its active execution through the existing guarded path." The console posts
//   the unified stop, and the runtime's answer now carries the mission's part: a Stop that ended a mission with no
//   controller run under it was TAKEN and is never shown as refused. No sentence claims that motion ceased.
//
//   A STOP THAT GETS NO ANSWER ENDS. The browser's connections to the server were once all held by camera streams, and
//   Stop's POST queued behind them with no answer and no end, the button stuck on "Stopping". The request is bounded
//   now: after STOP_ANSWER_TIMEOUT_MS it is aborted, read as not known, and never sent again.
//
// These drive the real HttpSource over a recorded transport and render the real screen to static markup; nothing is
// sent anywhere.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { HttpSource, STOP_ANSWER_TIMEOUT_MS } from '../api/http';
import type { FetchLike } from '../api/http';
import type { StopAck } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import type { MissionRecord } from '../model/missionRecord';
import { missionStartLabel, missionStartOff } from '../model/missionView';
import { applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { stopReceipt } from './receipts';
import { Screen } from './Screen';
import { pressStart } from './startPress';

interface Call {
  method: string;
  url: string;
  body: string | null;
  headers: Record<string, string>;
}

const BOOT = {
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-0001',
  credentials: { credential_header: 'x-console-credential', credential: 'a-per-launch-credential', request_header: 'x-console-request', request_value: '1' },
};

const HELLO = { ok: true, contract_version: '1', runtime_session: 'synthetic-session-0001', read_only: false, commands_enabled: true, credential_owner: 'runtime_session' };

/** The running fixture with its run ended, so the run slot is free: a mission may be started on it. */
function idleSnapshot(): Snapshot {
  const snapshot = JSON.parse(JSON.stringify(runningSnapshot)) as Record<string, unknown>;
  snapshot['active_run'] = null;
  return snapshot as unknown as Snapshot;
}

function recorded(route: (call: Call) => { status: number; body: unknown } | null): { calls: Call[]; store: ConsoleStore } {
  const calls: Call[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    const call: Call = { method: init?.method ?? 'GET', url, body: typeof init?.body === 'string' ? init.body : null, headers: (init?.headers ?? {}) as Record<string, string> };
    calls.push(call);
    const answer = route(call) ??
      (url.includes('/hello')
        ? { status: 200, body: HELLO }
        : url.includes('/snapshot')
          ? { status: 200, body: idleSnapshot() }
          : url.includes('/history')
            ? { status: 200, body: { ok: true, runs: [] } }
            : { status: 404, body: { ok: false, reason_code: 'not_found' } });
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body } as unknown as Response;
  };
  return { calls, store: new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null, boot: BOOT })) };
}

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

function liveView(): ViewState {
  return { ...applySnapshot(initialState(), idleSnapshot()), data_mode: 'live' };
}

function screen(patch: Partial<StoreSnapshot>, view: ViewState = liveView()): string {
  const store = new ConsoleStore(new SyntheticSource());
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), sourceKind: 'live', ...patch, view };
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
}

/** The opening tag of the element carrying `data-control="<name>"`, so its attributes can be read. */
function controlTag(markup: string, name: string): string {
  const match = new RegExp(`<[a-z]+[^>]*data-control="${name}"[^>]*>`).exec(markup);
  if (match === null) throw new Error(`no ${name} control in the markup`);
  return match[0];
}

describe('the header Start is the canonical mission start', () => {
  it('sends POST /api/mission/start, once, with the guard headers and an empty body, and never start_cycle', async () => {
    const { calls, store } = recorded((call) => {
      if (call.url.endsWith('/mission') && call.method === 'GET') return { status: 404, body: { ok: false, reason_code: 'no_mission_recorded' } };
      if (call.url.endsWith('/mission/start')) return { status: 200, body: { ok: true, mission_id: 'mission-0000000000aa' } };
      return null;
    });
    await store.start();
    expect(store.getSnapshot().missionError).toBe('no_mission_recorded');
    const receipt = await pressStart(store);
    const posts = calls.filter((call) => call.method === 'POST');
    expect(posts.map((call) => call.url)).toEqual(['/api/mission/start']);
    expect(posts[0]?.body).toBe('{}');
    expect(posts[0]?.headers['x-console-credential']).toBe('a-per-launch-credential');
    expect(calls.some((call) => call.url.includes('/commands'))).toBe(false);
    expect(receipt.tone).toBe('taken');
    expect(receipt.text).toContain('mission-0000000000aa');
    // The receipt is not the verdict: the mission record is read again after the start.
    expect(calls.filter((call) => call.url.endsWith('/mission') && call.method === 'GET').length).toBeGreaterThanOrEqual(2);
    store.dispose();
  });

  it('sends nothing on a runtime that serves no mission, and says why', async () => {
    const { calls, store } = recorded((call) =>
      call.url.endsWith('/mission') ? { status: 409, body: { ok: false, reason_code: 'mission_unavailable', detail: 'rehearsal_requires_synthetic_mode' } } : null,
    );
    await store.start();
    expect(store.getSnapshot().missionErrorDetail).toBe('rehearsal_requires_synthetic_mode');
    const receipt = await pressStart(store);
    expect(receipt.tone).toBe('refused');
    expect(receipt.text).toContain('nothing was sent');
    expect(calls.filter((call) => call.method === 'POST')).toEqual([]);
    store.dispose();
  });

  it('keeps the runtime refusal and its detail as a refusal, not a lost answer', async () => {
    const source = new HttpSource({
      fetchImpl: (async () =>
        ({ ok: false, status: 409, json: async () => ({ ok: false, reason_code: 'mission_already_running', detail: null }) }) as unknown as Response) as FetchLike,
      eventSourceFactory: null,
      boot: BOOT,
    });
    expect(await source.missionStart()).toEqual({ kind: 'refused', reason_code: 'mission_already_running', detail: null });
  });

  it('tells a surface with nothing started on it from a runtime with no mission route', async () => {
    const answer = (body: unknown) =>
      new HttpSource({
        fetchImpl: (async () => ({ ok: false, status: 404, json: async () => body }) as unknown as Response) as FetchLike,
        eventSourceFactory: null,
      }).mission();
    expect(await answer({ ok: false, reason_code: 'no_mission_recorded' })).toEqual({ ok: false, reason_code: 'no_mission_recorded', detail: null });
    expect(await answer({ ok: false, reason_code: 'not_found' })).toEqual({ ok: false, reason_code: 'this runtime does not report a mission', detail: null });
  });
});

describe('when a mission may be started', () => {
  const base = { mission: null, missionError: null, missionErrorDetail: null, runActive: false };
  const mission = (extra: Partial<MissionRecord>): MissionRecord =>
    ({
      mission_record_version: '1', mission_id: 'mission-1', state: 'RUNNING', data_mode: 'synthetic', performs_motion: false, execution_label: null,
      started_wall_utc: null, selected_target: null, waiting_reason: null, detail: null, targets: [], activations: [], ...extra,
    }) as MissionRecord;

  it('only on a surface that answered, with nothing carried and no run in the slot', () => {
    expect(missionStartOff({ ...base, missionError: 'no_mission_recorded' })).toBeNull();
    expect(missionStartOff({ ...base, mission: mission({ state: 'COMPLETE' }) })).toBeNull();
    expect(missionStartOff({ ...base, mission: mission({ state: 'STOPPED' }) })).toBeNull();
  });

  it('never on a guess, and never beside a mission or a run already going', () => {
    expect(missionStartOff(base)).toContain('has not been read yet');
    expect(missionStartOff({ ...base, missionError: 'the runtime did not answer' })).toBe('No mission can be started from here: the runtime did not answer.');
    expect(missionStartOff({ ...base, missionError: 'mission_unavailable', missionErrorDetail: 'cake_not_configured' })).toContain('no Cake resident is configured');
    expect(missionStartOff({ ...base, missionError: 'mission_unavailable', missionErrorDetail: 'something_newer' })).toContain('the runtime gives the reason something_newer');
    expect(missionStartOff({ ...base, missionError: 'no_mission_recorded', runActive: true })).toContain('A controller run is active');
    expect(missionStartOff({ ...base, mission: mission({ state: 'WAITING_FOR_CAPABILITY' }) })).toContain('is held');
    expect(missionStartOff({ ...base, mission: mission({ stop_requested: true }) })).toContain('is stopping');
    expect(missionStartOff({ ...base, mission: mission({ state: 'COMPLETE' }), missionError: 'the runtime did not answer' })).toContain('The last mission read failed');
  });

  it('labels the button with what the mission is doing', () => {
    expect(missionStartLabel({ ...base, mission: mission({}) }, false)).toBe('Running');
    expect(missionStartLabel({ ...base, mission: mission({ state: 'WAITING_FOR_CAPABILITY' }) }, false)).toBe('Held');
    expect(missionStartLabel({ ...base, mission: mission({ stop_requested: true }) }, false)).toBe('Stopping');
    expect(missionStartLabel({ ...base, missionError: 'no_mission_recorded' }, true)).toBe('Starting');
    expect(missionStartLabel({ ...base, missionError: 'no_mission_recorded' }, false)).toBe('Start');
  });
});

describe('the Start control on the screen', () => {
  it('is disabled by the attribute AND aria-disabled on a LIVE runtime with no mission surface, with the reason visible', () => {
    const markup = screen({ missionError: 'mission_unavailable', missionErrorDetail: 'rehearsal_requires_synthetic_mode' });
    const start = controlTag(markup, 'start');
    expect(start).toContain('disabled=""');
    expect(start).toContain('aria-disabled="true"');
    expect(start).toContain('aria-describedby="start-off-reason"');
    // VISIBLE: an element on the page with the id the button names, and the runtime's own reason in it.
    const reason = /<span[^>]*id="start-off-reason"[^>]*>([^<]*)<\/span>/.exec(markup);
    expect(reason).not.toBeNull();
    expect(reason?.[1]).toContain('This runtime serves no mission');
    expect(reason?.[1]).toContain('rehearsal_requires_synthetic_mode');
    expect(reason?.[1]).toContain('robot-side execution adapter');
  });

  it('is enabled, and says so to assistive technology, on a runtime whose mission surface has nothing started', () => {
    const view = { ...liveView(), data_mode: 'synthetic' as const };
    const markup = screen({ missionError: 'no_mission_recorded', missionErrorDetail: null }, view);
    const start = controlTag(markup, 'start');
    expect(start).not.toContain('disabled=""');
    expect(start).toContain('aria-disabled="false"');
    expect(markup).not.toContain('id="start-off-reason"');
  });

  it('is off while a mission is being carried, and points at Stop', () => {
    const mission = {
      mission_record_version: '1', mission_id: 'mission-00000000beef', state: 'RUNNING', data_mode: 'synthetic', performs_motion: false,
      execution_label: 'recording-rehearsal', started_wall_utc: '2026-09-24T10:00:00.000Z', selected_target: 'cube_near', waiting_reason: null,
      detail: null, targets: [], activations: [],
    } as MissionRecord;
    const markup = screen({ mission, missionError: null }, { ...liveView(), data_mode: 'synthetic' });
    expect(controlTag(markup, 'start')).toContain('disabled=""');
    expect(text(markup)).toContain('Start is off. Mission mission-00000000beef is being carried. Stop ends it');
  });

  it('keeps Stop reachable while Start is off', () => {
    const markup = screen({ missionError: 'mission_unavailable', missionErrorDetail: 'rehearsal_requires_synthetic_mode' });
    const stop = controlTag(markup, 'stop');
    expect(stop).not.toContain('disabled=""');
    expect(stop).toContain('aria-disabled="false"');
  });
});

describe('what a Stop says it stopped', () => {
  const answer = (body: unknown, status = 200): Promise<StopAck> =>
    new HttpSource({
      fetchImpl: (async () => ({ ok: status < 300, status, json: async () => body }) as unknown as Response) as FetchLike,
      eventSourceFactory: null,
      boot: BOOT,
    }).stop();

  const missionPart = { mission_id: 'mission-0000000000aa', stopping: true, reason_code: null, state: 'WAITING_FOR_CAPABILITY', stop_requested: true, carried: true };

  it('reads a stop that ended only a mission as TAKEN, never as refused', async () => {
    const ack = await answer({
      ok: true,
      reason_code: null,
      command: { command_id: 'c'.repeat(20), status: 'finished', reason_code: 'no_active_run', run_id: null },
      stop: { requested_mono_ns: null, run_id: null },
      mission: missionPart,
    });
    expect(ack.outcome).toBe('taken');
    expect(ack.run_id).toBeNull();
    expect(ack.mission?.stopping).toBe(true);
    const receipt = stopReceipt(ack);
    expect(receipt.tone).toBe('taken');
    expect(receipt.text).toContain('asked mission mission-0000000000aa to stop');
    expect(receipt.text).toContain('No controller run was active.');
    expect(receipt.text).toContain('Whether motion ceased is not in this answer');
    expect(receipt.text.toLowerCase()).not.toContain('refused');
  });

  it('reads the plain stop shape, with no stop object and a mission that is stopping, as taken too', async () => {
    const ack = await answer({ ok: true, stop: null, mission: { ...missionPart, state: 'STOPPED' } });
    expect(ack.outcome).toBe('taken');
    expect(stopReceipt(ack).text).toContain('ended mission mission-0000000000aa');
  });

  it('names the run it signalled and the mission it stopped when it did both', async () => {
    const ack = await answer({
      ok: true,
      reason_code: null,
      command: { command_id: 'c'.repeat(20), status: 'finished', reason_code: null, run_id: 'run-7' },
      stop: { requested_mono_ns: null, run_id: 'run-7' },
      mission: { ...missionPart, state: 'RUNNING' },
    });
    const receipt = stopReceipt(ack);
    expect(receipt.text).toContain('signalled the controller run run-7 to stop and asked mission mission-0000000000aa to stop');
    expect(receipt.text).not.toContain('No controller run was active');
  });

  it('says a stop that stopped neither was refused, and names no mission as stopped', async () => {
    const ack = await answer({
      ok: false,
      reason_code: 'no_active_run',
      command: { command_id: 'c'.repeat(20), status: 'finished', reason_code: 'no_active_run', run_id: null },
      stop: null,
      mission: { mission_id: null, stopping: false, reason_code: 'no_running_mission', state: null, stop_requested: false, carried: false },
    });
    expect(ack.outcome).toBe('refused');
    const receipt = stopReceipt(ack);
    expect(receipt.tone).toBe('refused');
    expect(receipt.text).toContain('Stop refused: no_active_run');
    expect(receipt.text).toContain('No mission was running either.');
  });

  it('never names the robot host or the resident as stopped', async () => {
    for (const body of [
      { ok: true, stop: { requested_mono_ns: 5, run_id: 'run-1' }, mission: null },
      { ok: true, stop: null, mission: missionPart },
    ]) {
      const words = stopReceipt(await answer(body)).text.toLowerCase();
      expect(words).not.toContain('host');
      expect(words).not.toContain('resident');
      expect(words).not.toContain('motion ceased.');
    }
  });

  it('says a stop with no answer is not known, which is not refused', async () => {
    const source = new HttpSource({
      fetchImpl: (async () => {
        throw new Error('the connection dropped');
      }) as FetchLike,
      eventSourceFactory: null,
      boot: BOOT,
    });
    const receipt = stopReceipt(await source.stop());
    expect(receipt.tone).toBe('unknown');
    expect(receipt.text).toContain('not known');
  });
});

describe('a Stop that gets no answer', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A transport that never answers, the way a request queued behind a full connection pool does not. */
  function silent(): { calls: { url: string; signal: AbortSignal | null }[]; fetchImpl: FetchLike } {
    const calls: { url: string; signal: AbortSignal | null }[] = [];
    const fetchImpl: FetchLike = (url, init) => {
      calls.push({ url, signal: init?.signal ?? null });
      return new Promise<Response>(() => undefined);
    };
    return { calls, fetchImpl };
  }

  it('ends as not known once the bound has passed, aborts the request, and sends nothing again', async () => {
    vi.useFakeTimers();
    const { calls, fetchImpl } = silent();
    const pending = new HttpSource({ fetchImpl, eventSourceFactory: null, boot: BOOT }).stop();
    let settled = false;
    void pending.then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(STOP_ANSWER_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const ack = await pending;
    expect(ack.outcome).toBe('unknown');
    expect(ack.reason).toContain('no answer to /stop within 15 s');
    expect(calls).toHaveLength(1);
    expect(calls[0]?.signal?.aborted).toBe(true);
    const receipt = stopReceipt(ack);
    expect(receipt.tone).toBe('unknown');
    expect(receipt.text).toContain('whether it reached the runtime is not known');
  });

  it('is bounded longer than the API gives the runtime, so an answer on its way is not cut short', () => {
    // console_api answers runtime_unavailable after RUNTIME_TIMEOUT_S = 10 s (apps/console-api/console_api/__main__.py).
    expect(STOP_ANSWER_TIMEOUT_MS).toBeGreaterThan(10_000);
  });

  it('keeps the stop on the store as not known, with the id it was sent under, so it can be asked about', async () => {
    vi.useFakeTimers();
    const { fetchImpl } = silent();
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null, boot: BOOT }));
    const pending = store.requestStop();
    await vi.advanceTimersByTimeAsync(STOP_ANSWER_TIMEOUT_MS);
    const ack = await pending;
    expect(store.getSnapshot().lastStop?.outcome).toBe('unknown');
    const tracked = store.getSnapshot().commands.find((item) => item.command_id === ack.command_id);
    expect(tracked?.unresolved).toContain('no answer to /stop');
  });

  it('bounds the mission stop door the same way', async () => {
    vi.useFakeTimers();
    const { fetchImpl } = silent();
    const pending = new HttpSource({ fetchImpl, eventSourceFactory: null, boot: BOOT }).missionStop();
    await vi.advanceTimersByTimeAsync(STOP_ANSWER_TIMEOUT_MS);
    expect((await pending).kind).toBe('unknown');
  });
});

describe('the mission stop door', () => {
  it('reads the mission part and whether the run part stopped a run', async () => {
    const answer = (body: unknown) =>
      new HttpSource({
        fetchImpl: (async () => ({ ok: true, status: 200, json: async () => body }) as unknown as Response) as FetchLike,
        eventSourceFactory: null,
        boot: BOOT,
      }).missionStop();
    const both = await answer({ ok: true, mission_id: 'mission-1', stopping: true, reason_code: null, state: 'RUNNING', carried: true, run_stop: { requested_mono_ns: 1, run_id: 'run-1' } });
    expect(both).toEqual({ kind: 'answered', mission: { mission_id: 'mission-1', stopping: true, reason_code: null, state: 'RUNNING', carried: true }, run_stopped: true });
    const missionOnly = await answer({ ok: true, mission_id: 'mission-1', stopping: true, reason_code: null, state: 'WAITING_FOR_CAPABILITY', carried: true, run_stop: null });
    expect(missionOnly.kind === 'answered' && missionOnly.run_stopped).toBe(false);
  });
});
