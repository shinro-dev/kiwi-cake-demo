// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { HttpSource } from './http';
import type { FetchLike } from './http';
import { mintCommandId } from './commandId';
import { ConsoleStore } from '../store/store';
import { SyntheticSource } from './synthetic';

interface Call {
  method: string;
  url: string;
  body: string | null;
}

function recorder(handler: (call: Call) => { status: number; body: unknown } | Error): { calls: Call[]; fetchImpl: FetchLike } {
  const calls: Call[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    const call: Call = {
      method: init?.method ?? 'GET',
      url,
      body: typeof init?.body === 'string' ? init.body : null,
    };
    calls.push(call);
    const answer = handler(call);
    if (answer instanceof Error) throw answer;
    return {
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json: async () => answer.body,
    } as unknown as Response;
  };
  return { calls, fetchImpl };
}

const START: Command = { type: 'start_cycle', args: { plan_only: false } };

describe('a lost response', () => {
  it('is resolved with GET /api/commands/{id} and the command is NEVER sent again', async () => {
    const { calls, fetchImpl } = recorder((call) => {
      if (call.method === 'POST') return new Error('the connection dropped before the response arrived');
      return { status: 200, body: { command_id: 'cmd-1', status: 'running', reason_code: null, run_id: 'run-1' } };
    });
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('cmd-1', START);

    expect(outcome.kind).toBe('result');
    if (outcome.kind === 'result') {
      expect(outcome.result.status).toBe('running');
      expect(outcome.result.run_id).toBe('run-1');
    }
    const posts = calls.filter((call) => call.method === 'POST');
    expect(posts.length).toBe(1);
    expect(calls.filter((call) => call.method === 'GET').map((call) => call.url)).toEqual(['/api/commands/cmd-1']);
  });

  it('says the outcome is unknown rather than resending when the resolve also fails', async () => {
    const { calls, fetchImpl } = recorder(() => new Error('the runtime is not answering'));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('cmd-2', START);

    expect(outcome.kind).toBe('unresolved');
    expect(calls.filter((call) => call.method === 'POST').length).toBe(1);
    expect(calls.filter((call) => call.method === 'GET').length).toBe(1);
  });

  it('resolves a command that answered with a body the console could not read, without resending', async () => {
    const { calls, fetchImpl } = recorder((call) => {
      if (call.method === 'POST') return { status: 200, body: { nothing: 'useful' } };
      return { status: 200, body: { command_id: 'cmd-3', status: 'uncertain', reason_code: 'runtime_restarted_before_child_recorded', run_id: null } };
    });
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('cmd-3', START);

    expect(outcome.kind).toBe('result');
    if (outcome.kind === 'result') expect(outcome.result.status).toBe('uncertain');
    expect(calls.filter((call) => call.method === 'POST').length).toBe(1);
  });

  it('sends one whole contract command: the five required fields, on the one object', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { command_id: 'cmd-4', status: 'accepted', reason_code: null, run_id: null } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null, sessionId: 'tab-session-1' });
    await source.submit('cmd-4', START);
    const post = calls[0] as Call;
    // This is packages/demo-contracts/schema/v1/command.schema.json: required contract_version, command_id, type, args
    // and client{session_id, contract_version}, additionalProperties false. The earlier version of this test asserted a
    // WRAPPED body, {command_id, command: {type, args}, contract_version, client}, on the reading that
    // handle_post_command takes the version off the envelope. It does not: _unwrap replaces the body with the inner
    // object first, so the version check then ran against a command that stated none. Measured against a running API,
    // every command sent that way came back rejected with reason_code contract_incompatible, and the inner object
    // carried no client.session_id either, which the runtime's own validation requires.
    expect(JSON.parse(post.body as string)).toEqual({
      contract_version: '1',
      command_id: 'cmd-4',
      type: 'start_cycle',
      args: START.args,
      client: { session_id: 'tab-session-1', contract_version: '1' },
    });
  });

  it('mints its own client session id when nothing gave it one, and keeps it across commands', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { command_id: 'cmd-4b', status: 'accepted', reason_code: null, run_id: null } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    await source.submit('cmd-4b', START);
    await source.submit('cmd-4c', START);
    const first = JSON.parse((calls[0] as Call).body as string) as { client: { session_id: string } };
    const second = JSON.parse((calls[1] as Call).body as string) as { client: { session_id: string } };
    // common.schema.json#/$defs/id is [A-Za-z0-9_.-]{1,64}. The runtime validates it, so a tab that minted one of
    // another shape would have every command refused.
    expect(first.client.session_id).toMatch(/^[A-Za-z0-9_.-]{1,64}$/);
    expect(second.client.session_id).toBe(first.client.session_id);
  });

  it('sends a whole contract command on the stop route too', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, stop: { requested_mono_ns: 1, run_id: 'run-1' } } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null, sessionId: 'tab-session-2' });
    await source.stop();
    const body = JSON.parse((calls[0] as Call).body as string) as Record<string, unknown>;
    expect(body['type']).toBe('stop');
    expect(body['args']).toEqual({});
    expect(body['contract_version']).toBe('1');
    expect(body['client']).toEqual({ session_id: 'tab-session-2', contract_version: '1' });
    expect(typeof body['command_id']).toBe('string');
  });

  it('refuses a status the contract does not have instead of showing it', async () => {
    const { fetchImpl } = recorder((call) => {
      if (call.method === 'POST') return { status: 200, body: { command_id: 'cmd-5', status: 'probably_fine', reason_code: null, run_id: null } };
      return new Error('nothing to resolve with');
    });
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const outcome = await source.submit('cmd-5', START);
    expect(outcome.kind).toBe('unresolved');
  });
});

describe('the handshake', () => {
  it('states the version this console speaks, so the server refuses a mismatch outright', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, contract_version: '1', runtime_session: 'r', commands_enabled: true } }));
    const source = new HttpSource({ fetchImpl, eventSourceFactory: null });
    const hello = await source.hello();
    expect(calls[0]?.url).toBe('/api/hello?contract_version=1');
    expect(hello.commands_enabled).toBe(true);
  });

  it('treats a server that does not say commands are enabled as one that has not said yes', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, contract_version: '1', runtime_session: 'r', read_only: true, commands_enabled: false, reason_code: 'replay_only' } }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    await store.start();
    const view = store.getSnapshot().view;
    // The contract is fine, so the record is still followed; only commanding is off, and it says why.
    expect(view.contract_compatible).toBe(true);
    expect(view.commands_enabled).toBe(false);
    expect(view.read_only_reason).toBe('replay_only');
    store.dispose();
  });
});

describe('an incompatible contract version', () => {
  it('makes the tab read-only from the hello alone', async () => {
    const { calls, fetchImpl } = recorder(() => ({ status: 200, body: { ok: false, reason_code: 'contract_incompatible' } }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    await store.start();

    expect(store.getSnapshot().view.contract_compatible).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('contract_incompatible');
    // Read-only means read-only: nothing else was asked of the runtime.
    expect(calls.map((call) => call.url)).toEqual(['/api/hello?contract_version=1']);
    store.dispose();
  });

  it('refuses a version that is not 1 even when the runtime says ok', async () => {
    const { fetchImpl } = recorder(() => ({ status: 200, body: { ok: true, contract_version: '2', runtime_session: 'x' } }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    await store.start();
    expect(store.getSnapshot().view.contract_compatible).toBe(false);
    expect(store.getSnapshot().view.read_only_reason).toBe('contract_incompatible');
    store.dispose();
  });
});

describe('command ids', () => {
  it('are random from the first character and do not repeat', () => {
    const ids = new Set<string>();
    const firsts = new Set<string>();
    for (let i = 0; i < 500; i += 1) {
      const id = mintCommandId();
      expect(id).toMatch(/^[a-z0-9]{24}$/);
      ids.add(id);
      firsts.add(id.slice(0, 1));
    }
    expect(ids.size).toBe(500);
    expect(firsts.size).toBeGreaterThan(8);
  });
});

describe('the store submits once', () => {
  it('tracks one command per submit and never queues a second', async () => {
    const source = new SyntheticSource();
    const store = new ConsoleStore(source);
    const first = await store.submit(START);
    expect(first.result?.status).toBe('running');
    const second = await store.submit(START);
    expect(second.result?.status).toBe('rejected');
    expect(second.result?.reason_code).toBe('run_active');
    expect(store.getSnapshot().commands.length).toBe(2);
    store.dispose();
  });

  it('resolves an unresolved command through the runtime, not by resending', async () => {
    let dropPost = true;
    const { calls, fetchImpl } = recorder((call) => {
      if (call.method === 'POST' && dropPost) return new Error('lost');
      if (call.method === 'GET' && call.url.startsWith('/api/commands/')) {
        return dropPost ? new Error('not yet') : { status: 200, body: { command_id: call.url.slice('/api/commands/'.length), status: 'finished', reason_code: null, run_id: 'run-9' } };
      }
      return { status: 200, body: {} };
    });
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: null }));
    const track = await store.submit(START);
    expect(track.unresolved).not.toBeNull();

    dropPost = false;
    await store.resolveCommand(track.command_id);
    const tracked = store.getSnapshot().commands.find((item) => item.command_id === track.command_id);
    expect(tracked?.result?.status).toBe('finished');
    expect(tracked?.unresolved).toBeNull();
    expect(calls.filter((call) => call.method === 'POST').length).toBe(1);
    store.dispose();
  });
});
