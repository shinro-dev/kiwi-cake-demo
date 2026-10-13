// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT A TAB GETS BACK WITHOUT ASKING THE BROWSER TO REMEMBER ANYTHING.
//
// A console that kept its pending command in localStorage would lose it on another machine, in another profile, in a
// private window and after a cleared cache, and would be wrong the moment the runtime disagreed with it. The runtime's
// snapshot already carries the command id of the run in flight, that run's id and the seq it started at, so a reloaded
// tab recovers all three from the server that actually knows them. These tests pin that, and the three other things a
// reconnect must not get wrong: a stop is a command with an identity, it aims at the LIVE run even while a recording
// is open, and a credential this server has stopped accepting turns commanding off instead of being retried.

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
}

function recorder(handler: (call: Call) => { status: number; body: unknown } | Error): { calls: Call[]; fetchImpl: FetchLike } {
  const calls: Call[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    const call: Call = { method: init?.method ?? 'GET', url, body: typeof init?.body === 'string' ? init.body : null };
    calls.push(call);
    const answer = handler(call);
    if (answer instanceof Error) throw answer;
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body } as unknown as Response;
  };
  return { calls, fetchImpl };
}

const RUNNING = runningSnapshot as unknown as Snapshot;
const HELLO = { ok: true, contract_version: '1', runtime_session: 'synthetic-session-0001', read_only: false, commands_enabled: true, credential_owner: 'runtime_session' };

/** The receipt the runtime answers for the command its snapshot says is running. */
const RECEIPT = {
  command_id: 'synthetic-command-0001',
  status: 'running',
  reason_code: null,
  run_id: 'synthetic-run-0001',
  payload_digest: 'sha256:' + 'ab'.repeat(32),
  first_seen_wall_utc: '2026-09-21T10:00:00.000Z',
};

function stackOver(handler: (call: Call) => { status: number; body: unknown } | Error) {
  const { calls, fetchImpl } = recorder(handler);
  return { calls, store: new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null })) };
}

function defaultStack(overrides: (call: Call) => { status: number; body: unknown } | Error | null = () => null) {
  return stackOver((call) => {
    const override = overrides(call);
    if (override !== null) return override;
    if (call.url.includes('/hello')) return { status: 200, body: HELLO };
    if (call.url.includes('/snapshot')) return { status: 200, body: RUNNING };
    if (call.url === '/api/commands/synthetic-command-0001') return { status: 200, body: RECEIPT };
    if (call.url.includes('/history')) return { status: 200, body: { ok: true, runs: [] } };
    return { status: 404, body: { ok: false, reason_code: 'not_found' } };
  });
}

describe('a tab that comes back while a run is in flight', () => {
  it('recovers the pending command, its run and its cursor from the snapshot alone', async () => {
    const { store } = defaultStack();
    await store.start();
    const recovered = store.getSnapshot().commands;
    expect(recovered.length).toBe(1);
    const track = recovered[0];
    expect(track?.command_id).toBe('synthetic-command-0001');
    expect(track?.result?.run_id).toBe('synthetic-run-0001');
    expect(track?.started_seq).toBe(1);
    // It says plainly that this tab did not send it. "The runtime says this is running" is a different claim from
    // "I sent this", and a reloaded tab may only make the second one.
    expect(track?.adopted).toBe(true);
    // The cursor the event stream resumes from is the snapshot's, not anything this browser kept.
    expect(store.getSnapshot().view.seq_cursor).toBe(19);
    store.dispose();
  });

  it('takes the admission time from the runtime and never from its own clock', async () => {
    const { store } = defaultStack();
    await store.start();
    expect(store.getSnapshot().commands[0]?.first_seen_wall_utc).toBe('2026-09-21T10:00:00.000Z');
    store.dispose();
  });

  it('adopts a command with a READ and never a second submit, and only once', async () => {
    const { calls, store } = defaultStack();
    await store.start();
    await store.refreshHistory();
    // Two more snapshot polls over the same active run must not add a second track or ask again.
    await store.refreshSnapshot();
    await store.refreshSnapshot();
    expect(store.getSnapshot().commands.length).toBe(1);
    expect(calls.filter((call) => call.method === 'POST')).toEqual([]);
    expect(calls.filter((call) => call.url === '/api/commands/synthetic-command-0001').length).toBe(1);
    store.dispose();
  });

  it('keeps the track when the runtime will not answer the receipt, rather than losing the command', async () => {
    const { store } = defaultStack((call) => (call.url === '/api/commands/synthetic-command-0001' ? new Error('the runtime did not answer') : null));
    await store.start();
    const track = store.getSnapshot().commands[0];
    expect(track?.command_id).toBe('synthetic-command-0001');
    expect(track?.result?.status).toBe('running');
    expect(track?.first_seen_wall_utc).toBeNull();
    store.dispose();
  });
});

describe('the stop the console sends', () => {
  it('is tracked under the id it was sent with, so a lost answer is asked about and never pressed again', async () => {
    const { calls, store } = defaultStack((call) =>
      call.url === '/api/stop'
        ? { status: 200, body: { ok: true, stop: { requested_mono_ns: null, run_id: 'synthetic-run-0001' }, command: { command_id: 'ignored', status: 'finished', reason_code: null, run_id: 'synthetic-run-0001' } } }
        : null,
    );
    await store.start();
    const ack = await store.requestStop();
    expect(ack.outcome).toBe('taken');
    const sent = JSON.parse(calls.filter((call) => call.url === '/api/stop')[0]?.body as string) as Record<string, unknown>;
    expect(sent['command_id']).toBe(ack.command_id);
    expect(store.getSnapshot().commands.some((track) => track.command_id === ack.command_id && track.type === 'stop')).toBe(true);
    store.dispose();
  });

  it('aims at the live run and names no run at all, even with a recording open', async () => {
    const { calls, store } = defaultStack((call) => {
      if (call.url === '/api/stop') return { status: 200, body: { ok: true, stop: { requested_mono_ns: null, run_id: 'synthetic-run-0001' } } };
      if (call.url.startsWith('/api/history/')) return { status: 200, body: { ok: true, run_id: 'an-older-run', events: [], origins: [] } };
      return null;
    });
    await store.start();
    await store.openReplay('an-older-run');
    const ack = await store.requestStop();
    const body = JSON.parse(calls.filter((call) => call.url === '/api/stop')[0]?.body as string) as Record<string, unknown>;
    // The replay is a fold of records on a screen. It is not what the robot is doing, and nothing about it travels
    // with the stop: the body carries a stop command and no run id of any kind.
    expect(JSON.stringify(body)).not.toContain('an-older-run');
    expect(body['type']).toBe('stop');
    expect(ack.run_id).toBe('synthetic-run-0001');
    store.dispose();
  });

  it('reports a stop that found no run as not taken', async () => {
    const { store } = defaultStack((call) =>
      call.url === '/api/stop'
        ? { status: 200, body: { ok: false, reason_code: 'no_active_run', stop: null, command: { command_id: 'x', status: 'finished', reason_code: 'no_active_run', run_id: null } } }
        : null,
    );
    await store.start();
    const ack = await store.requestStop();
    expect(ack.ok).toBe(false);
    expect(ack.outcome).toBe('refused');
    expect(ack.reason).toBe('no_active_run');
    store.dispose();
  });
});

describe('a credential this server has stopped accepting', () => {
  it('turns commanding off with the server reason and replays nothing', async () => {
    const { calls, store } = defaultStack((call) =>
      call.method === 'POST' && call.url === '/api/commands' ? { status: 403, body: { ok: false, reason_code: 'credential_superseded' } } : null,
    );
    await store.start();
    const track = await store.submit({ type: 'observe', args: { holding: false } });
    expect(track.result?.status).toBe('rejected');
    expect(track.result?.reason_code).toBe('credential_superseded');
    expect(store.getSnapshot().session.credential_superseded).toBe(true);
    expect(store.getSnapshot().view.commands_enabled).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('credential_superseded');
    // Exactly one POST. A refusal is not a lost response and there is nothing here to send again.
    expect(calls.filter((call) => call.method === 'POST').length).toBe(1);
    store.dispose();
  });

  it('stops commanding when the runtime answering is not the one this tab did readiness against', async () => {
    let session = 'synthetic-session-0001';
    const { store } = defaultStack((call) =>
      call.url.includes('/snapshot') ? { status: 200, body: { ...RUNNING, runtime_session: session } } : null,
    );
    await store.start();
    expect(store.getSnapshot().session.booted_runtime_session).toBe('synthetic-session-0001');
    session = 'synthetic-session-0002';
    await store.refreshSnapshot();
    expect(store.getSnapshot().session.runtime_replaced).toBe(true);
    expect(store.getSnapshot().view.commands_enabled).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('runtime_session_changed');
    store.dispose();
  });
});
