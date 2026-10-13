// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT A TAB MAY DO TO A MISSION WITHOUT BEING TOLD TO.
//
// The answer is nothing. A mission moves a robot and an activation changes what a resident holds, and both of them are
// things a page must only ever do because somebody pressed something. The failure mode is not hypothetical: a console
// that recovered its state by REPLAYING what it last did would start a mission on every reload, and one that retried a
// lost activation would activate twice. So these tests drive the store over a recorded transport and assert on the
// REQUESTS: what a mount sends, what an activation sends, and what is not sent again.
//
// They also pin the request's shape. The activation body carries a candidate key and nothing else: no bytes, no path
// and no operation name. That is not a style preference. A browser that could name the thing to be admitted would be
// choosing what the resident runs, and no amount of guarding elsewhere would take that back.

import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { HttpSource } from '../api/http';
import type { FetchLike } from '../api/http';
import { ConsoleStore } from './store';

interface Call {
  method: string;
  url: string;
  body: string | null;
  headers: Record<string, string>;
}

const RUNNING = runningSnapshot as unknown as Snapshot;
const HELLO = {
  ok: true,
  contract_version: '1',
  runtime_session: 'synthetic-session-0001',
  read_only: false,
  commands_enabled: true,
  credential_owner: 'runtime_session',
};

/** The boot element a page served by the API carries. The activation must present both of its headers. */
const BOOT = {
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-0001',
  credentials: {
    credential_header: 'x-console-credential',
    credential: 'a-per-launch-credential',
    request_header: 'x-console-request',
    request_value: '1',
  },
};

const WAITING_MISSION = {
  mission_record_version: '1',
  mission_id: 'mission-0001',
  state: 'WAITING_FOR_CAPABILITY',
  data_mode: 'live',
  started_wall_utc: '2026-09-23T10:00:00.000Z',
  selected_target: 'cube-b',
  waiting_reason: 'capability_unavailable',
  detail: 'the second cube is beyond the arm from where the base is standing',
  targets: [
    { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: ['run-a1'] },
    {
      target_id: 'cube-b',
      label: 'Cube B',
      reachable: false,
      requires_capability: true,
      state: 'WAITING_FOR_CAPABILITY',
      runs: [],
    },
  ],
  activations: [],
};

const CANDIDATES = { ok: true, candidates: [{ key: 'candidate-0001', payload_digest: 'sha256:' + 'ab'.repeat(32) }] };

function recorder(handler: (call: Call) => { status: number; body: unknown } | Error | Promise<{ status: number; body: unknown }>): {
  calls: Call[];
  fetchImpl: FetchLike;
} {
  const calls: Call[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    const call: Call = {
      method: init?.method ?? 'GET',
      url,
      body: typeof init?.body === 'string' ? init.body : null,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const answer = await handler(call);
    if (answer instanceof Error) throw answer;
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body } as unknown as Response;
  };
  return { calls, fetchImpl };
}

function stack(overrides: (call: Call) => { status: number; body: unknown } | Error | Promise<{ status: number; body: unknown }> | null = () => null) {
  const { calls, fetchImpl } = recorder((call) => {
    const override = overrides(call);
    if (override !== null) return override;
    if (call.url.includes('/hello')) return { status: 200, body: HELLO };
    if (call.url.includes('/snapshot')) return { status: 200, body: RUNNING };
    if (call.url === '/api/commands/synthetic-command-0001') {
      return { status: 200, body: { command_id: 'synthetic-command-0001', status: 'running', reason_code: null, run_id: 'synthetic-run-0001' } };
    }
    if (call.url.endsWith('/mission/candidates')) return { status: 200, body: CANDIDATES };
    if (call.url.endsWith('/mission')) return { status: 200, body: WAITING_MISSION };
    if (call.url.includes('/history')) return { status: 200, body: { ok: true, runs: [] } };
    return { status: 404, body: { ok: false, reason_code: 'not_found' } };
  });
  return { calls, store: new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null, boot: BOOT })) };
}

describe('a tab that opens, or comes back, on a mission that is waiting', () => {
  it('recovers the mission from the runtime without sending anything at all', async () => {
    const { calls, store } = stack();
    await store.start();
    const mission = store.getSnapshot().mission;
    expect(mission?.mission_id).toBe('mission-0001');
    expect(mission?.state).toBe('WAITING_FOR_CAPABILITY');
    expect(mission?.targets.length).toBe(2);
    // THE WHOLE POINT. A mount reads. It does not start a mission and it does not activate anything, so a reload
    // cannot do either, whatever the page was in the middle of when it was reloaded.
    expect(calls.every((call) => call.method === 'GET')).toBe(true);
    expect(calls.some((call) => call.url.includes('/mission/activate'))).toBe(false);
    expect(calls.some((call) => call.url.includes('/mission/start'))).toBe(false);
    store.dispose();
  });

  it('reads the prepared candidates while it is waiting, and does not read them again on the next poll', async () => {
    const { calls, store } = stack();
    await store.start();
    expect(store.getSnapshot().missionCandidates?.map((item) => item.key)).toEqual(['candidate-0001']);
    const reads = () => calls.filter((call) => call.url.endsWith('/mission/candidates')).length;
    expect(reads()).toBe(1);
    await store.refreshMission();
    // A prepared candidate is prepared. Re-reading the list under the operator's finger between choosing one and
    // pressing the button would change what the button sends.
    expect(reads()).toBe(1);
    store.dispose();
  });

  it('keeps the mission it holds when a later read fails, and says why beside it', async () => {
    let fail = false;
    const { store } = stack((call) => (fail && call.url.endsWith('/mission') ? new Error('the runtime did not answer') : null));
    await store.start();
    expect(store.getSnapshot().mission?.mission_id).toBe('mission-0001');
    fail = true;
    await store.refreshMission();
    // A runtime that hiccuped did not become a runtime with no mission.
    expect(store.getSnapshot().mission?.mission_id).toBe('mission-0001');
    expect(store.getSnapshot().missionError).not.toBe(null);
    store.dispose();
  });

  it('reports a runtime that carries no mission as that, and never as an empty mission', async () => {
    const { store } = stack((call) => (call.url.endsWith('/mission') ? { status: 200, body: { ok: false, reason_code: 'no_mission' } } : null));
    await store.start();
    expect(store.getSnapshot().mission).toBe(null);
    expect(store.getSnapshot().missionError).toBe('no_mission');
    store.dispose();
  });
});

describe('the prepared activation', () => {
  it('sends the candidate key and nothing else, under the headers the guard requires', async () => {
    const { calls, store } = stack((call) =>
      call.url.endsWith('/mission/activate')
        ? {
            status: 200,
            body: {
              ok: true,
              operation_uuid: 'op-0001',
              activation_state: 'SUBMITTED',
              outcome: null,
              transaction_phase: 'PREPARED',
              detail: null,
            },
          }
        : null,
    );
    await store.start();
    await store.activateCandidate('candidate-0001');

    const post = calls.find((call) => call.url.endsWith('/mission/activate'));
    expect(post?.method).toBe('POST');
    const body = JSON.parse(post?.body ?? '{}') as Record<string, unknown>;
    // ONE KEY. Not a path, not bytes, not an operation name, and nothing else the runtime did not ask for.
    expect(Object.keys(body)).toEqual(['candidate_key']);
    expect(body['candidate_key']).toBe('candidate-0001');
    expect(post?.headers['x-console-credential']).toBe('a-per-launch-credential');
    expect(post?.headers['x-console-request']).toBe('1');
    expect(post?.headers['content-type']).toBe('application/json');
    store.dispose();
  });

  it('reads the mission back afterwards, so the durable history is the runtime record and not this tab memory', async () => {
    const submitted = {
      ...WAITING_MISSION,
      activations: [
        {
          operation_uuid: 'op-0001',
          target_id: 'cube-b',
          candidate_key: 'candidate-0001',
          payload_digest: 'sha256:' + 'ab'.repeat(32),
          activation_state: 'SUBMITTED',
          outcome: null,
          transaction_phase: 'PREPARED',
          transaction_uuid: 'tx-0001',
          detail: null,
          t_wall_utc: '2026-09-23T10:05:00.000Z',
        },
      ],
    };
    let activated = false;
    const { store } = stack((call) => {
      if (call.url.endsWith('/mission/activate')) {
        activated = true;
        return { status: 200, body: { ok: true, operation_uuid: 'op-0001', activation_state: 'SUBMITTED', outcome: null, transaction_phase: 'PREPARED', detail: null } };
      }
      if (call.url.endsWith('/mission') && activated) return { status: 200, body: submitted };
      return null;
    });
    await store.start();
    expect(store.getSnapshot().mission?.activations.length).toBe(0);
    await store.activateCandidate('candidate-0001');
    expect(store.getSnapshot().mission?.activations.map((item) => item.operation_uuid)).toEqual(['op-0001']);
    expect(store.getSnapshot().lastActivation).toEqual({
      kind: 'answered',
      operation_uuid: 'op-0001',
      activation_state: 'SUBMITTED',
      outcome: null,
      transaction_phase: 'PREPARED',
      detail: null,
    });
    store.dispose();
  });

  it('refuses a second request while one is in flight rather than queueing or resending it', async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { calls, store } = stack((call) =>
      call.url.endsWith('/mission/activate')
        ? held.then(() => ({ status: 200, body: { ok: true, operation_uuid: 'op-0001', activation_state: 'SUBMITTED', outcome: null, transaction_phase: null, detail: null } }))
        : null,
    );
    await store.start();
    const first = store.activateCandidate('candidate-0001');
    const second = await store.activateCandidate('candidate-0001');
    expect(second).toEqual({
      kind: 'refused',
      reason_code: 'an activation is already in flight in this tab; nothing was sent a second time',
    });
    expect(calls.filter((call) => call.url.endsWith('/mission/activate')).length).toBe(1);
    release();
    await first;
    expect(calls.filter((call) => call.url.endsWith('/mission/activate')).length).toBe(1);
    store.dispose();
  });

  it('calls a refusal by the guard a refusal: it was decided before the runtime was touched', async () => {
    const { store } = stack((call) =>
      call.url.endsWith('/mission/activate') ? { status: 403, body: { ok: false, reason_code: 'credential_superseded' } } : null,
    );
    await store.start();
    const outcome = await store.activateCandidate('candidate-0001');
    expect(outcome).toEqual({ kind: 'refused', reason_code: 'credential_superseded' });
    store.dispose();
  });

  it('calls a lost answer UNKNOWN, never "it did not land", and sends nothing again', async () => {
    const { calls, store } = stack((call) => (call.url.endsWith('/mission/activate') ? new Error('the connection dropped') : null));
    await store.start();
    const outcome = await store.activateCandidate('candidate-0001');
    expect(outcome.kind).toBe('unknown');
    // The one claim nobody may make about a request whose fate is not known.
    if (outcome.kind === 'unknown') expect(outcome.detail).not.toContain('did not land');
    expect(calls.filter((call) => call.url.endsWith('/mission/activate')).length).toBe(1);
    store.dispose();
  });
});
