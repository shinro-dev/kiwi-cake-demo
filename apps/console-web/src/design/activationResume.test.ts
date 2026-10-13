// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AFTER AN OBSERVED ACTIVATION THE PRESSING DISPLAY LEAVES "WAITING FOR CAKE" AND TURNS THE CANDIDATES OFF WITHIN A FRACTION
// OF A SECOND OF THE RUNTIME'S RESUME, NOT AT THE NEXT POLL (redesign r4 verify-r4 must_fix 2).
//
// The runtime appends the activation's answer before it returns it (`demo_runtime/mission_coordinator.py`
// submit_activation) and writes the resume only at the carrying thread's next slot read (`CAPABILITY_POLL_S` 0.5 s), so
// the one mission read the store made right after the answer still said WAITING_FOR_CAPABILITY. Nothing read the mission
// again until the 5 s poll unless a run started: the card said "Mission waiting for Cake" and both candidates were on
// beside the "Activated" card while the runtime's record already said COMPLETE (a rehearsal) or RUNNING, and a press then
// was refused `mission_not_waiting_for_capability` (the judge's JC-R: 70 samples of 50 ms up to +3750 ms). Here the
// runtime resumes 300 ms after its answer, as the judge's harness did, and no sample later than 600 ms after the answer
// may still wait for Cake or offer a candidate.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import idleSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/idle-first-start.json';
import type { Capability } from '@kiwi/demo-contracts';
import { HttpSource } from '../api/http';
import type { EventSourceLike, FetchLike } from '../api/http';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { element } from './demoScenarioPlay';

const SESSION = 'synthetic-session-0000';
const KEY = 'candidate-0001';
const DIGEST = 'sha256:' + 'ab'.repeat(32);

function hello(mode: string): Record<string, unknown> {
  return {
    ok: true, reason_code: null, contract_version: '1', read_only: false, commands_enabled: true, runtime_session: SESSION,
    credential_owner: 'runtime_session', bound_runtime_session: SESSION,
    runtime: { reachable: true, session: SESSION, contract_version: '1', data_mode: mode },
  };
}

const CARRIED: Capability = { id: 'start_cycle', state: 'blocked', reason_code: 'mission_running', reason: 'a mission is being carried: only stop is accepted', dependency: 'owner', planned: false };

function capabilities(carried: boolean): Capability[] {
  const off = (id: string): Capability => ({ ...CARRIED, id });
  const on = (id: string): Capability => ({ id, state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false });
  return carried
    ? [off('start_cycle'), off('return_arm'), off('scan_scene'), off('observe'), on('stop')]
    : [on('start_cycle'), on('return_arm'), on('scan_scene'), on('observe'), { id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', dependency: null, planned: false }];
}

const SLOT = (generation: number, active: boolean): Record<string, unknown> => ({
  read: 'ok', slot_uuid: 'slot-0001', name: 'base', state: 'ACTIVE', capsule: active ? 'capsule-swapped' : 'capsule-inert',
  generation, instance: 1, session_uuid: 'resident-session-0001', plan_digest: null, epoch: 1, capability_active: active,
});

function activation(state: 'OBSERVED' | 'REJECTED_BY_CAKE'): Record<string, unknown> {
  return {
    operation_uuid: state === 'OBSERVED' ? 'operation-0002' : 'operation-0001',
    target_id: 'cube',
    candidate_key: KEY,
    payload_digest: DIGEST,
    activation_state: state,
    outcome: state === 'OBSERVED' ? 'ACCEPTED' : 'REJECTED',
    transaction_phase: state === 'OBSERVED' ? 'COMPLETED' : null,
    transaction_uuid: null,
    detail: null,
    t_wall_utc: '2026-09-28T09:48:31.000Z',
    slot_before: SLOT(1, false),
    slot_after: state === 'OBSERVED' ? SLOT(2, true) : SLOT(1, false),
  };
}

type MissionState = 'WAITING_FOR_CAPABILITY' | 'RUNNING' | 'COMPLETE';

function mission(state: MissionState, activations: Record<string, unknown>[]): Record<string, unknown> {
  const target = state === 'WAITING_FOR_CAPABILITY' ? 'WAITING_FOR_CAPABILITY' : state === 'RUNNING' ? 'ACTIVE' : 'PLACED';
  return {
    mission_record_version: 1,
    mission_id: 'mission-0001',
    state,
    data_mode: 'synthetic',
    performs_motion: false,
    execution_label: 'recording-rehearsal',
    started_wall_utc: '2026-09-28T09:48:00.000Z',
    selected_target: 'cube',
    waiting_reason: state === 'WAITING_FOR_CAPABILITY' ? 'capability_unavailable' : null,
    detail: null,
    stop_requested: false,
    stop_requested_wall_utc: null,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: target, runs: [], attempts: [], retries: [], park_runs: [] }],
    activations,
    hold: null,
    next_actions: state === 'COMPLETE' ? ['start'] : ['stop'],
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

interface Runtime {
  source: HttpSource;
  reads: string[];
  answeredAt: () => number | null;
  /** What the runtime's record says now. */
  set: (next: Record<string, unknown>) => void;
  current: () => Record<string, unknown>;
}

/**
 * A runtime whose mission waits for Cake. The activation's answer is `answer`; the record read right after it is WAITING
 * with that activation appended, as the runtime writes it.
 */
function runtime(mode: string, answer: 'OBSERVED' | 'REJECTED_BY_CAKE'): Runtime {
  const reads: string[] = [];
  let record = mission('WAITING_FOR_CAPABILITY', []);
  let answeredAt: number | null = null;
  const fetchImpl: FetchLike = async (input, init) => {
    const method = (init?.method ?? 'GET').toUpperCase();
    reads.push(`${method} ${input}`);
    if (method === 'POST' && input === '/api/mission/activate') {
      answeredAt = Date.now();
      const written = activation(answer);
      record = mission('WAITING_FOR_CAPABILITY', [written]);
      return json(200, {
        ok: true, mission_id: 'mission-0001', operation_uuid: written['operation_uuid'], activation_state: answer, outcome: written['outcome'],
        transaction_phase: written['transaction_phase'], detail: null, stopped_during_activation: false, transaction: null,
        slot_before: written['slot_before'], slot_after: written['slot_after'],
      });
    }
    if (method !== 'GET') return json(599, { ok: false, reason_code: 'this_test_sends_nothing_else' });
    if (input.startsWith('/api/hello')) return json(200, hello(mode));
    if (input === '/api/snapshot') {
      const carried = record['state'] === 'RUNNING' || record['state'] === 'WAITING_FOR_CAPABILITY';
      return json(200, { ...idleSnapshot, runtime_session: SESSION, data_mode: mode, active_run: null, capabilities: capabilities(carried) });
    }
    if (input === '/api/mission') return json(200, record);
    if (input === '/api/mission/candidates') return json(200, { ok: true, candidates: [{ key: KEY, payload_digest: DIGEST }] });
    if (input.startsWith('/api/history')) return json(200, { ok: true, runs: [] });
    return json(404, { ok: false, reason_code: 'not_served_in_this_test' });
  };
  const eventSourceFactory = (): EventSourceLike => ({ onmessage: null, onerror: null, close: () => undefined });
  return {
    source: new HttpSource({ fetchImpl, eventSourceFactory, boot: null, sessionId: 'activation-resume-test' }),
    reads,
    answeredAt: () => answeredAt,
    set: (next) => {
      record = next;
    },
    current: () => record,
  };
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function cardVerdict(markup: string): string | null {
  return /data-verdict="([^"]*)"/.exec(element(markup, 'data-outcome-card=') ?? '')?.[1] ?? null;
}

function candidatesOn(markup: string): number {
  return [...markup.matchAll(/<button[^>]*data-control="candidate"[^>]*>/g)].filter((match) => /aria-disabled="false"/.test(match[0])).length;
}

function readsAfterAnswer(reads: readonly string[], what: string): number {
  const at = reads.indexOf('POST /api/mission/activate');
  return at < 0 ? 0 : reads.slice(at + 1).filter((read) => read === what).length;
}

describe('the pressing display after an OBSERVED activation whose first mission read still says WAITING', () => {
  afterEach(() => vi.useRealTimers());

  for (const variant of ['COMPLETE', 'RUNNING'] as const) {
    it(`leaves "waiting for Cake" and turns the candidates off within 600 ms of the answer (the runtime resumes to ${variant} 300 ms after it, no run.started)`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-28T09:48:30.000Z') });
      const rt = runtime(variant === 'COMPLETE' ? 'synthetic' : 'live', 'OBSERVED');
      const store = new ConsoleStore(rt.source, { storage: null });
      await store.start();
      await vi.advanceTimersByTimeAsync(1200);
      const before = render(store);
      expect(cardVerdict(before)).toBe('waiting_cake');
      expect(candidatesOn(before)).toBe(1);

      await store.activateCandidate(KEY);
      const answered = rt.answeredAt();
      expect(answered).not.toBeNull();
      // The read right after the answer, as the runtime serves it: still WAITING, with the OBSERVED answer on the record.
      expect(store.getSnapshot().mission?.state).toBe('WAITING_FOR_CAPABILITY');

      const late: string[] = [];
      let resumed = false;
      for (let k = 0; k < 60; k += 1) {
        const t = Date.now() - (answered as number);
        if (!resumed && t >= 300) {
          resumed = true;
          rt.set(mission(variant, [activation('OBSERVED')]));
        }
        const markup = render(store);
        const verdict = cardVerdict(markup);
        const on = candidatesOn(markup);
        if (t > 600 && (verdict === 'waiting_cake' || on > 0)) late.push(`+${String(t)} ms card=${String(verdict)} candidatesOn=${String(on)}`);
        await vi.advanceTimersByTimeAsync(50);
      }
      expect(late).toEqual([]);
      const after = render(store);
      expect(cardVerdict(after)).toBe(variant === 'COMPLETE' ? 'complete' : 'running');
      // The snapshot is read once the mission is seen to leave WAITING, before the next poll would have read it.
      expect(readsAfterAnswer(rt.reads, 'GET /api/snapshot')).toBeGreaterThan(0);
      store.dispose();
    }, 60_000);
  }

  it('reads the mission again for at most 2 s while the record keeps an OBSERVED answer and still says WAITING, and then leaves it to the poll', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T09:48:30.000Z') });
    const rt = runtime('synthetic', 'OBSERVED');
    const store = new ConsoleStore(rt.source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1200);
    await store.activateCandidate(KEY);
    // The runtime never resumes. 4.5 s after the answer the poll has not come round yet (it runs every 5 s from start).
    await vi.advanceTimersByTimeAsync(2500);
    const reads = readsAfterAnswer(rt.reads, 'GET /api/mission');
    // The read right after the answer, and one every 250 ms for 2 s: at most 9, and never a read loop.
    expect(reads).toBeGreaterThan(1);
    expect(reads).toBeLessThanOrEqual(9);
    await vi.advanceTimersByTimeAsync(700);
    expect(readsAfterAnswer(rt.reads, 'GET /api/mission')).toBe(reads);
    store.dispose();
  }, 60_000);

  it('does not read again after a refusal: the mission still waits, and that is what the record says', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-28T09:48:30.000Z') });
    const rt = runtime('synthetic', 'REJECTED_BY_CAKE');
    const store = new ConsoleStore(rt.source, { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1200);
    await store.activateCandidate(KEY);
    await vi.advanceTimersByTimeAsync(2500);
    expect(readsAfterAnswer(rt.reads, 'GET /api/mission')).toBe(1);
    expect(cardVerdict(render(store))).toBe('waiting_cake');
    expect(rt.current()['state']).toBe('WAITING_FOR_CAPABILITY');
    store.dispose();
  }, 60_000);
});
