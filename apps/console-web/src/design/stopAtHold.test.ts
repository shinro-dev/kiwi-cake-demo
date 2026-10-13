// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AFTER STOP AT A HELD OR WAITING MISSION NO REGION SAYS THE MISSION IS STILL CARRIED BESIDE THE CARD THAT SAYS IT STOPPED
// (redesign r4 verify-r3 must_fix 1).
//
// The stop's answer re-read the mission only. A stop at a hold ends no run, so no run.finished re-read the snapshot either,
// and for up to one 5 s poll the snapshot's `start_cycle: blocked mission_running` stayed on the screen: the top bar said
// red "Not ready: A mission is being carried: only stop is accepted.", Start and Return arm were off with that reason, all
// beside "Mission stopped on request ... Ended HH:MM:SSZ" (the judge's JA: 65, 10, 50, 10 and 10 samples of 50 ms on the
// pressing display). The display now reads the snapshot again when the stop is answered and when the mission record says
// the mission ended, and while the snapshot still says `mission_running` beside a mission record that has ended it says
// so in its own voice, never the runtime's "being carried" as current. Sampled every 50 ms for 6 s after the press, on the
// pressing display and on a second display of the same source, as the judge did.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import type { Capability } from '@kiwi/demo-contracts';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import { MISSION_ENDED_NOT_IDLE, missionEndedNotIdle } from '../panels/commandGuards';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { readScreen, settle } from './demoScenarioPlay';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

/** The card's ids that say the mission has ended. */
const TERMINAL = new Set(['stopped_on_request', 'complete', 'failed']);

/** Where the two regions disagree: an ended card beside "being carried" in the readiness line or in any control's title. */
function contradiction(markup: string, store: ConsoleStore): string | null {
  const read = readScreen(markup, store);
  if (read.outcome.mission === null || !TERMINAL.has(read.outcome.mission)) return null;
  const carriedLine = read.readiness === 'not_ready' && /being carried/.test(read.readinessSentence);
  const carriedTitle = /title="[^"]*being carried[^"]*"/.exec(markup)?.[0] ?? null;
  if (!carriedLine && carriedTitle === null) return null;
  return `card=${read.outcome.mission} readiness=${read.readiness} "${read.readinessSentence}" ${carriedTitle ?? ''}`;
}

describe('Stop at a held or waiting mission, on the pressing display and on a second display', () => {
  afterEach(() => vi.useRealTimers());

  for (const name of ['one-block', 'near-block-grasp-miss', 'hold-live-default', 'swap-then-stopped-holding', 'tampered-refused-before-transaction'] as ScenarioName[]) {
    it(`${name}: no sample puts "being carried" beside the card that says the mission ended`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-28T05:00:00Z') });
      const source = new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(name)) });
      const pressing = new ConsoleStore(source, { storage: null });
      const watching = new ConsoleStore(source, { storage: null });
      await pressing.start();
      await watching.start();
      await settle(pressing);
      await settle(watching);
      await pressStart(pressing);
      let held = false;
      for (let i = 0; i < 600 && !held; i += 1) {
        await vi.advanceTimersByTimeAsync(250);
        await settle(pressing);
        await settle(watching);
        const mission = pressing.getSnapshot().mission;
        held = mission?.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason !== null;
      }
      expect(held).toBe(true);
      // THE SNAPSHOT SETTLES FIRST: the hold has been on both displays for longer than a poll.
      await vi.advanceTimersByTimeAsync(6000);
      await settle(pressing);
      await settle(watching);
      const ack = await pressing.requestStop();
      expect(ack.outcome).toBe('taken');
      const bad: string[] = [];
      const stale: string[] = [];
      let sawEnded = false;
      for (let k = 0; k <= 120; k += 1) {
        for (const [label, store] of [['pressing', pressing], ['watching', watching]] as const) {
          await settle(store);
          const markup = render(store);
          const found = contradiction(markup, store);
          if (found !== null) bad.push(`${label} +${String(k * 50)} ms ${found}`);
          if (label === 'pressing' && TERMINAL.has(readScreen(markup, store).outcome.mission ?? '')) sawEnded = true;
        }
        // READ AGAIN AT THE ANSWER: from 100 ms after it the pressing display holds no list or check record that still
        // says mission_running, where the source it reads no longer does.
        const snap = pressing.getSnapshot();
        const held = [
          ...snap.view.capabilities.filter((item) => item.reason_code === 'mission_running').map((item) => `capability ${item.id}`),
          ...(snap.capabilityCheck?.off_reason === 'mission_running' ? ['capability check'] : []),
          ...(snap.signatureCheck?.off_reason === 'mission_running' ? ['signature check'] : []),
        ];
        if (k >= 2 && held.length > 0) stale.push(`+${String(k * 50)} ms ${held.join(', ')}`);
        await vi.advanceTimersByTimeAsync(50);
      }
      expect(stale).toEqual([]);
      expect(sawEnded).toBe(true);
      expect(bad).toEqual([]);
      pressing.dispose();
      watching.dispose();
    }, 180_000);
  }
});

/* ---- the runtime path: a snapshot that keeps `mission_running` after the stop's answer ------------------------------- */

const SESSION = 'synthetic-session-0000';
const HELLO = {
  ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: SESSION,
  credential_owner: 'runtime_session', bound_runtime_session: SESSION,
  runtime: { reachable: true, session: SESSION, contract_version: '1', data_mode: 'live' },
};

const MISSION_RUNNING: Capability = { id: 'start_cycle', state: 'blocked', reason_code: 'mission_running', reason: 'a mission is being carried: only stop is accepted', dependency: 'owner', planned: false };

function capabilities(carried: boolean): Capability[] {
  const off = (id: string): Capability => ({ ...MISSION_RUNNING, id });
  const on = (id: string): Capability => ({ id, state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false });
  return carried
    ? [off('start_cycle'), off('return_arm'), off('scan_scene'), off('observe'), on('stop')]
    : [on('start_cycle'), on('return_arm'), on('scan_scene'), on('observe'), { id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', dependency: null, planned: false }];
}

function mission(state: 'WAITING_FOR_CAPABILITY' | 'STOPPED'): Record<string, unknown> {
  return {
    mission_record_version: '1',
    mission_id: 'mission-0001',
    state,
    data_mode: 'live',
    started_wall_utc: '2026-09-28T05:00:00.000Z',
    ended_wall_utc: state === 'STOPPED' ? '2026-09-28T05:00:40.000Z' : null,
    selected_target: 'cube',
    waiting_reason: 'capability_unavailable',
    detail: null,
    stop_requested: state === 'STOPPED',
    stop_requested_wall_utc: state === 'STOPPED' ? '2026-09-28T05:00:40.000Z' : null,
    next_actions: state === 'STOPPED' ? ['return_arm', 'start'] : ['stop'],
    targets: [{ target_id: 'cube', label: 'Cube', reachable: false, requires_capability: true, state, runs: [] }],
    activations: [],
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

/** A runtime whose mission record reads STOPPED at the stop's answer while its snapshot keeps `mission_running` for `staleReads` more reads. */
function runtime(staleReads: number): { reads: string[]; source: HttpSource } {
  const reads: string[] = [];
  const state = { stopped: false, stale: staleReads };
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    reads.push(`${method} ${input}`);
    if (method === 'POST' && input === '/api/stop') {
      state.stopped = true;
      return json(200, { ok: true, command: null, stop: null, mission: { mission_id: 'mission-0001', stopping: true, reason_code: null, state: 'STOPPED', carried: false } });
    }
    if (input.startsWith('/api/hello')) return json(200, HELLO);
    if (input === '/api/snapshot') {
      const carried = !state.stopped || state.stale > 0;
      if (state.stopped && state.stale > 0) state.stale -= 1;
      return json(200, { ...idleSnapshot, runtime_session: SESSION, data_mode: 'live', capabilities: capabilities(carried) });
    }
    if (input === '/api/mission') return json(200, mission(state.stopped ? 'STOPPED' : 'WAITING_FOR_CAPABILITY'));
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  return { reads, source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'stop-at-hold-test' }) };
}

function snapshotReadsAfterStop(reads: readonly string[]): number {
  const at = reads.indexOf('POST /api/stop');
  return at < 0 ? 0 : reads.slice(at + 1).filter((read) => read === 'GET /api/snapshot').length;
}

describe('the store over the runtime: a stop answered with a mission part', () => {
  afterEach(() => vi.useRealTimers());

  it('reads the snapshot again when the stop is answered, and once more a second later while it still says mission_running', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T05:00:30.000Z') });
    const { reads, source } = runtime(1);
    const store = new ConsoleStore(source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(100);
    expect(store.getSnapshot().mission?.state).toBe('WAITING_FOR_CAPABILITY');
    await store.requestStop();
    await vi.advanceTimersByTimeAsync(50);
    // Read again at the answer: this read still says mission_running.
    expect(snapshotReadsAfterStop(reads)).toBe(1);
    expect(store.getSnapshot().mission?.state).toBe('STOPPED');
    expect(store.getSnapshot().view.capabilities.find((item) => item.id === 'start_cycle')?.reason_code).toBe('mission_running');
    const words = readScreen(render(store), store);
    expect(words.outcome.mission).toBe('stopped_on_request');
    expect(words.readinessSentence).not.toMatch(/being carried/);
    expect(words.readinessSentence).toBe('Mission mission-0001 has ended by its own record; the runtime has not yet reported itself idle.');
    expect(words.markup).not.toMatch(/title="[^"]*being carried[^"]*"/);
    expect(words.off['start']).toBe(true);
    expect(words.off['stop']).toBe(false);
    // Once more a second later, well before the next poll: the runtime now reports itself idle.
    await vi.advanceTimersByTimeAsync(1100);
    expect(snapshotReadsAfterStop(reads)).toBe(2);
    expect(store.getSnapshot().view.capabilities.find((item) => item.id === 'start_cycle')?.state).toBe('enabled');
    expect(readScreen(render(store), store).readinessSentence).not.toMatch(/has not yet reported itself idle/);
    // And no more reads than that until the poll.
    await vi.advanceTimersByTimeAsync(2000);
    expect(snapshotReadsAfterStop(reads)).toBe(2);
    store.dispose();
  });
});

describe('the rule the controls read', () => {
  it("says the snapshot's mission_running in the display's voice only while the mission record this display holds has ended", () => {
    const ended = mission('STOPPED') as never;
    const carried = mission('WAITING_FOR_CAPABILITY') as never;
    expect(missionEndedNotIdle(MISSION_RUNNING, ended)).toBe(`mission mission-0001 ${MISSION_ENDED_NOT_IDLE}`);
    expect(missionEndedNotIdle(MISSION_RUNNING, carried)).toBeNull();
    expect(missionEndedNotIdle(MISSION_RUNNING, null)).toBeNull();
    expect(missionEndedNotIdle({ ...MISSION_RUNNING, reason_code: 'run_active' }, ended)).toBeNull();
    expect(missionEndedNotIdle({ ...MISSION_RUNNING, state: 'enabled', reason_code: null }, ended)).toBeNull();
    expect(missionEndedNotIdle(null, ended)).toBeNull();
    for (const state of ['COMPLETE', 'FAILED']) expect(missionEndedNotIdle(MISSION_RUNNING, { ...(ended as object), state } as never)).not.toBeNull();
  });
});
