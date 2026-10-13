// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ROBOT SURFACE OVER HTTP, AND THE ONE REQUEST THAT GRANTS PERMISSION TO MOVE.
//
// Three properties, each the reason a piece of this exists:
//
//   A PAGE LOAD READS AND NEVER WRITES. A reload recovers the robot, the mission and the hold from the runtime without
//   confirming, starting or submitting anything.
//   THE CONFIRMATION POSTS WHAT WAS DISPLAYED. Exactly the session and the host instance the operator saw, through the
//   same client and the same guard headers every command carries, and nothing else in the body.
//   OUT-OF-ORDER READS NEVER LAND. An answer to a read sent before one already applied is dropped whole.
//   A FAILED READ KEEPS THE LAST ANSWER, marked as retained, rather than replacing the robot with nothing.

import { describe, expect, it } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { readBoot } from './boot';
import { HttpSource } from './http';
import type { FetchLike } from './http';
import { ConsoleStore } from '../store/store';
import { FIXTURE_HOST_INSTANCE, FIXTURE_SESSION, robotBody } from '../model/robotStatusFixture';

const BOOT_JSON = JSON.stringify({
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-session-1',
  credential: 'not-a-real-credential-value',
  credential_header: 'X-Console-Credential',
  request_header: 'X-Console-Request',
});

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: string | null;
}

function fetchWith(robot: () => { status: number; body: unknown }, confirm?: () => { status: number; body: unknown }) {
  const calls: Call[] = [];
  const impl: FetchLike = async (url: string, init?: RequestInit) => {
    calls.push({
      url,
      method: (init?.method ?? 'GET').toUpperCase(),
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? init.body : null,
    });
    const answer = url.includes('/robot/confirm-session')
      ? (confirm ?? (() => ({ status: 200, body: { ok: true, confirmed_session_uuid: FIXTURE_SESSION, motion_permitted: true } })))()
      : url.includes('/robot')
        ? robot()
        : {
            status: 200,
            body: url.includes('/hello')
              ? { ok: true, contract_version: '1', runtime_session: 'runtime-session-a', read_only: false, commands_enabled: true }
              : url.includes('/snapshot')
                ? runningSnapshot
                : url.includes('/history')
                  ? { ok: true, runs: [] }
                  : url.includes('/mission')
                    ? { ok: false, reason_code: 'no_mission_recorded' }
                    : {},
          };
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body } as unknown as Response;
  };
  return { impl, calls };
}

function bootDocument(text: string) {
  return { getElementById: (id: string) => (id === 'kc-console-boot' ? { textContent: text } : null) };
}

describe('reading the robot over HTTP', () => {
  it('is a GET of /api/robot and reads the status it serves', async () => {
    const { impl, calls } = fetchWith(() => ({ status: 200, body: robotBody() }));
    const answer = await new HttpSource({ fetchImpl: impl }).robotStatus();
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual(['GET /api/robot']);
    expect(answer.ok && answer.status.resident.session_uuid).toBe(FIXTURE_SESSION);
  });

  it('refuses a status version it does not speak, and says an older runtime reports no robot', async () => {
    const newer = await new HttpSource({ fetchImpl: fetchWith(() => ({ status: 200, body: robotBody({ robot_status_version: '9' }) })).impl }).robotStatus();
    expect(newer.ok).toBe(false);
    const older = await new HttpSource({ fetchImpl: fetchWith(() => ({ status: 404, body: null })).impl }).robotStatus();
    expect(!older.ok && older.reason_code).toBe('this runtime does not report a robot');
  });
});

describe('confirming the connected robot', () => {
  it('posts exactly the session and the host instance it was given, with the guard headers, and nothing else', async () => {
    const { impl, calls } = fetchWith(() => ({ status: 200, body: robotBody() }));
    const source = new HttpSource({ fetchImpl: impl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
    // An object carrying something extra: the body is rebuilt field by field, so the extra never travels.
    const outcome = await source.confirmRobotSession(FIXTURE_SESSION, { ...FIXTURE_HOST_INSTANCE, force: true } as typeof FIXTURE_HOST_INSTANCE);
    expect(outcome.kind).toBe('answered');
    expect(calls).toHaveLength(1);
    const call = calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.url).toBe('/api/robot/confirm-session');
    expect(JSON.parse(call?.body ?? 'null')).toEqual({ session_uuid: FIXTURE_SESSION, host_instance: FIXTURE_HOST_INSTANCE });
    // The u64 handle travels as the exact decimal string, never as a rounded number.
    expect(call?.body).toContain('"slot_instance":"6003298303284871169"');
    // The same headers every mutating request carries: the json content type and the two boot headers.
    expect(call?.headers['content-type']).toBe('application/json');
    expect(call?.headers['X-Console-Credential']).toBe('not-a-real-credential-value');
    expect(Object.keys(call?.headers ?? {})).toContain('X-Console-Request');
  });

  it('reports a guard refusal as refused and a lost answer as unknown, and never resends', async () => {
    const refused = fetchWith(() => ({ status: 200, body: robotBody() }), () => ({ status: 409, body: { ok: false, reason_code: 'robot_session_mismatch' } }));
    const a = await new HttpSource({ fetchImpl: refused.impl }).confirmRobotSession(FIXTURE_SESSION, FIXTURE_HOST_INSTANCE);
    expect(a).toEqual({ kind: 'refused', reason_code: 'robot_session_mismatch' });
    const lost = fetchWith(() => ({ status: 200, body: robotBody() }), () => ({ status: 502, body: null }));
    const b = await new HttpSource({ fetchImpl: lost.impl }).confirmRobotSession(FIXTURE_SESSION, FIXTURE_HOST_INSTANCE);
    expect(b.kind).toBe('unknown');
    expect(lost.calls.filter((call) => call.method === 'POST')).toHaveLength(1);
  });
});

describe('the store holding the robot', () => {
  it('recovers the robot on a load without writing anything', async () => {
    const { impl, calls } = fetchWith(() => ({ status: 200, body: robotBody() }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl: impl, eventSourceFactory: null }));
    await store.start();
    store.dispose();
    expect(store.getSnapshot().robot?.resident.session_uuid).toBe(FIXTURE_SESSION);
    expect(store.getSnapshot().robotReadAtMs).not.toBeNull();
    // A reload reads the robot, the mission and the hold back. It confirms nothing, starts nothing, submits nothing.
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([]);
    expect(calls.some((call) => call.url === '/api/robot')).toBe(true);
  });

  it('keeps the last good answer when a later read fails, and says why beside it', async () => {
    let fail = false;
    const { impl } = fetchWith(() => (fail ? { status: 503, body: { ok: false, reason_code: 'runtime_unavailable' } } : { status: 200, body: robotBody() }));
    const store = new ConsoleStore(new HttpSource({ fetchImpl: impl, eventSourceFactory: null }));
    await store.start();
    fail = true;
    await store.refreshRobot();
    store.dispose();
    expect(store.getSnapshot().robot?.resident.session_uuid).toBe(FIXTURE_SESSION);
    expect(store.getSnapshot().robotError).toBe('runtime_unavailable');
  });

  it('sends one confirmation, refuses a second while it is in flight, and reads the robot again after it', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { impl, calls } = fetchWith(() => ({ status: 200, body: robotBody() }));
    const slow: FetchLike = async (url, init) => {
      if (url.includes('/confirm-session')) await gate;
      return impl(url, init);
    };
    const store = new ConsoleStore(new HttpSource({ fetchImpl: slow, eventSourceFactory: null }));
    const first = store.confirmRobotSession(FIXTURE_SESSION, FIXTURE_HOST_INSTANCE);
    const second = await store.confirmRobotSession(FIXTURE_SESSION, FIXTURE_HOST_INSTANCE);
    expect(second.kind).toBe('refused');
    release();
    await first;
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1);
    const posted = calls.findIndex((call) => call.method === 'POST');
    expect(calls.slice(posted + 1).some((call) => call.url === '/api/robot')).toBe(true);
    expect(store.getSnapshot().lastConfirm?.kind).toBe('answered');
  });

  it('drops an answer to an older read that arrives after a newer one was applied', async () => {
    const releases: (() => void)[] = [];
    let session = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const { impl } = fetchWith(() => ({ status: 200, body: robotBody({ resident: { ...(robotBody().resident as object), session_uuid: session } }) }));
    const slow: FetchLike = async (url, init) => {
      if (url === '/api/robot') {
        const answer = impl(url, init); // the body is decided NOW, when the read is sent
        await new Promise<void>((resolve) => releases.push(resolve));
        return answer;
      }
      return impl(url, init);
    };
    const store = new ConsoleStore(new HttpSource({ fetchImpl: slow, eventSourceFactory: null }));
    const older = store.refreshRobot();
    session = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const newer = store.refreshRobot();
    releases[1]?.();
    await newer;
    releases[0]?.();
    await older;
    expect(store.getSnapshot().robot?.resident.session_uuid).toBe('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');
  });
});
