// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Reconciling an uncertain command, over HTTP.
//
// The property every test here is about is the same one: resolving is a STATEMENT and never a retry. No request this
// file makes carries a command, no code path re-sends one, and the block is never cleared by this console. The last is
// the subtle one: `cleared` on the answer is the runtime's report of what IT decided, and the controls only come back
// when the next snapshot says so, which is why the store refreshes the snapshot after every attempt.

import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { HttpSource } from './http';
import type { FetchLike } from './http';
import { parseBoot } from './boot';
import { SyntheticSource } from './synthetic';
import { ConsoleStore } from '../store/store';

interface Call {
  method: string;
  url: string;
  body: string | null;
  headers: Record<string, string>;
}

function recorder(handler: (call: Call) => { status: number; body: unknown } | Error): { calls: Call[]; fetchImpl: FetchLike } {
  const calls: Call[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    const call: Call = {
      method: init?.method ?? 'GET',
      url,
      body: typeof init?.body === 'string' ? init.body : null,
      headers: (init?.headers ?? {}) as Record<string, string>,
    };
    calls.push(call);
    const answer = handler(call);
    if (answer instanceof Error) throw answer;
    return {
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: async () => {
        if (answer.body === undefined) throw new Error('no body');
        return answer.body;
      },
    } as unknown as Response;
  };
  return { calls, fetchImpl };
}

/** The shape apps/console-api injects into the page it serves. The value here is not a real credential. */
const BOOT = parseBoot({
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-session-1',
  credential: 'not-a-real-credential-value',
  credential_header: 'X-Console-Credential',
  request_header: 'X-Console-Request',
});

const COMMAND_ID = 'abcd1234ef0123456789';

const CLEARED = {
  ok: true,
  command_id: COMMAND_ID,
  owner: 'free',
  cleared: true,
  cleared_by: 'operator_attestation',
  classification: 'ran_and_was_interrupted',
  evidence: { process: { alive: false, readable: true }, run_files: { readable: true, terminal_record: false } },
  attestation: { finding: 'controller_stopped_by_operator', note: 'the arm was at rest', classification: 'ran_and_was_interrupted', clears_block: true, refused: null, t_wall_utc: '2026-01-01T00:01:00.000Z' },
};

describe('the resolve request', () => {
  it('goes to the id in the PATH, carries no command, and presents what the guard requires', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: CLEARED }));
    const source = new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null });
    await source.resolveUncertain(COMMAND_ID, 'controller_stopped_by_operator', 'the arm was at rest');

    expect(calls).toHaveLength(1);
    const call = calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.url).toBe(`/api/commands/${COMMAND_ID}/resolve`);
    expect(call?.headers['content-type']).toBe('application/json');
    expect(call?.headers['X-Console-Request']).toBe('1');
    expect(call?.headers['X-Console-Credential']).toBe('not-a-real-credential-value');

    const body = JSON.parse(call?.body ?? '{}') as Record<string, unknown>;
    expect(body).toEqual({ finding: 'controller_stopped_by_operator', note: 'the arm was at rest' });
    // The one thing that must not be in it: a command.
    expect(Object.keys(body)).not.toContain('type');
    expect(Object.keys(body)).not.toContain('args');
    expect(Object.keys(body)).not.toContain('command');
  });

  it('sends a null note rather than an empty string when there is nothing to say', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: CLEARED }));
    const source = new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null });
    await source.resolveUncertain(COMMAND_ID, 'no_controller_running', null);
    expect(JSON.parse(calls[0]?.body ?? '{}')).toEqual({ finding: 'no_controller_running', note: null });
  });

  it('reports what the runtime found, and never decides it here', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: CLEARED }));
    const source = new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null });
    const outcome = await source.resolveUncertain(COMMAND_ID, 'controller_stopped_by_operator', null);
    expect(outcome.kind).toBe('answered');
    if (outcome.kind !== 'answered') return;
    expect(outcome.cleared).toBe(true);
    expect(outcome.cleared_by).toBe('operator_attestation');
    expect(outcome.classification).toBe('ran_and_was_interrupted');
    expect(outcome.evidence).not.toBeNull();
    expect(outcome.attestation?.finding).toBe('controller_stopped_by_operator');
  });
});

describe('a refusal is an answer, and the block stays on', () => {
  it('reads a live controller as a refusal with the runtime reason and the contradiction', async () => {
    const { fetchImpl } = recorder(() => ({
      status: 409,
      body: {
        ok: false,
        reason_code: 'controller_alive',
        cleared: false,
        contradiction: { declared: 'no_controller_running', found: "a process with this command's identity is alive now" },
        evidence: { process: { alive: true, readable: true } },
      },
    }));
    const source = new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null });
    const outcome = await source.resolveUncertain(COMMAND_ID, 'no_controller_running', null);
    if (outcome.kind !== 'answered') throw new Error('expected an answer');
    expect(outcome.cleared).toBe(false);
    expect(outcome.reason_code).toBe('controller_alive');
    expect(outcome.contradiction).not.toBeNull();
  });

  it('treats an answer that does not say cleared as one that did not say yes', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, classification: 'still_unknown', reason_code: 'evidence_unreadable' } }));
    const source = new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null });
    const outcome = await source.resolveUncertain(COMMAND_ID, 'no_controller_running', null);
    if (outcome.kind !== 'answered') throw new Error('expected an answer');
    expect(outcome.cleared).toBe(false);
    expect(outcome.cleared_by).toBeNull();
    expect(outcome.classification).toBe('still_unknown');
  });

  it('says the fate of the request is unknown rather than assuming anything, when nothing came back', async () => {
    const { fetchImpl } = recorder(() => new Error('the connection dropped'));
    const source = new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null });
    const outcome = await source.resolveUncertain(COMMAND_ID, 'no_controller_running', null);
    expect(outcome.kind).toBe('unknown');
  });

  it('keeps a guard refusal as a refusal with the server reason, and never as a lost request', async () => {
    const { fetchImpl } = recorder(() => ({ status: 403, body: { ok: false, reason_code: 'credential_invalid' } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.resolveUncertain(COMMAND_ID, 'no_controller_running', null);
    if (outcome.kind !== 'answered') throw new Error('expected an answer');
    expect(outcome.reason_code).toBe('credential_invalid');
    expect(outcome.cleared).toBe(false);
  });
});

describe('the store, resolving', () => {
  function sourceOver(handler: (call: Call) => { status: number; body: unknown } | Error) {
    const { calls, fetchImpl } = recorder(handler);
    return { calls, source: new HttpSource({ fetchImpl, boot: BOOT, eventSourceFactory: null }) };
  }

  it('re-reads the snapshot afterwards, because that is what says whether the block is gone', async () => {
    const { calls, source } = sourceOver((call) => {
      if (call.url.endsWith('/resolve')) return { status: 200, body: CLEARED };
      if (call.url.endsWith('/snapshot')) return { status: 200, body: runningSnapshot as unknown as Snapshot };
      return { status: 404, body: { ok: false, reason_code: 'not_found' } };
    });
    const store = new ConsoleStore(source);
    await store.resolveUncertain(COMMAND_ID, 'controller_stopped_by_operator', null);
    // The exact calls, in order, and no other. The third one is the RECEIPT of the command the re-read snapshot says
    // is running: the store adopts an active run it did not submit, and reads that command by id. The fourth reads that
    // run's records so far, which this tab's stream will never deliver (redesign verdict item 5). Both are GETs, they
    // carry no command, and there is no second POST anywhere in this list.
    expect(calls.map((call) => call.url)).toEqual([
      `/api/commands/${COMMAND_ID}/resolve`,
      '/api/snapshot',
      '/api/commands/synthetic-command-0001',
      '/api/history/synthetic-run-0001/events',
    ]);
    expect(calls.filter((call) => call.method === 'POST').map((call) => call.url)).toEqual([`/api/commands/${COMMAND_ID}/resolve`]);
    expect(store.getSnapshot().lastResolution?.kind).toBe('answered');
    // Nothing in the store cleared the block: the fixture snapshot carries no unresolved command, so it is null
    // BECAUSE the server said so on the re-read.
    expect(store.getSnapshot().view.unresolved).toBeNull();
    expect(store.getSnapshot().resolving).toBe(false);
  });

  it('keeps the block on when the runtime refused the statement, whatever the operator declared', async () => {
    const blocked = {
      ...(runningSnapshot as unknown as Record<string, unknown>),
      unresolved_command: { command_id: COMMAND_ID, block_reason_code: 'owner_unresolved', findings: ['no_controller_running'] },
    };
    const { source } = sourceOver((call) => {
      if (call.url.endsWith('/resolve')) return { status: 409, body: { ok: false, reason_code: 'controller_alive', cleared: false } };
      if (call.url.endsWith('/snapshot')) return { status: 200, body: blocked };
      return { status: 404, body: { ok: false, reason_code: 'not_found' } };
    });
    const store = new ConsoleStore(source);
    await store.resolveUncertain(COMMAND_ID, 'no_controller_running', null);
    expect(store.getSnapshot().view.unresolved?.command_id).toBe(COMMAND_ID);
    expect(store.getSnapshot().lastResolution?.kind).toBe('answered');
  });

  it('keeps the live block visible while a recorded run is open on the screen', async () => {
    const blocked = {
      ...(runningSnapshot as unknown as Record<string, unknown>),
      unresolved_command: { command_id: COMMAND_ID, block_reason_code: 'owner_unresolved', findings: ['no_controller_running'] },
    };
    const { source } = sourceOver((call) => {
      if (call.url.endsWith('/snapshot')) return { status: 200, body: blocked };
      if (call.url.includes('/history/')) return { status: 200, body: { events: [] } };
      return { status: 200, body: { ok: true, runs: [] } };
    });
    const store = new ConsoleStore(source);
    await store.start();
    expect(store.getSnapshot().liveUnresolved?.command_id).toBe(COMMAND_ID);

    // Open a replay. The folded recording carries no live block, and the view's own field goes null with it.
    await store.openReplay('a-recorded-run');
    expect(store.getSnapshot().view.unresolved).toBeNull();
    // The robot in the room is still in an unknown state, and the panel that says so still has it.
    expect(store.getSnapshot().liveUnresolved?.command_id).toBe(COMMAND_ID);
    store.dispose();
  });

  it('never sends a command on any of it', async () => {
    const { calls, source } = sourceOver((call) => {
      if (call.url.endsWith('/resolve')) return { status: 200, body: CLEARED };
      return { status: 200, body: runningSnapshot as unknown as Snapshot };
    });
    const store = new ConsoleStore(source);
    await store.resolveUncertain(COMMAND_ID, 'controller_finished', null);
    expect(calls.filter((call) => call.url === '/api/commands')).toEqual([]);
    for (const call of calls) expect(call.body === null || !call.body.includes('"type"')).toBe(true);
  });
});

describe('the synthetic source', () => {
  it('refuses to reconcile rather than answering that it found nothing', async () => {
    const outcome = await new SyntheticSource().resolveUncertain();
    if (outcome.kind !== 'answered') throw new Error('expected an answer');
    expect(outcome.cleared).toBe(false);
    expect(outcome.classification).toBe('still_unknown');
    expect(outcome.reason_code).toBe('synthetic_source');
    expect(outcome.evidence).toBeNull();
  });
});
