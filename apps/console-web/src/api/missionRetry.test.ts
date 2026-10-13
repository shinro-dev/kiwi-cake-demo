// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// TRY AGAIN, over HTTP: POST /api/mission/retry, the mission's own door for a fresh attempt after a failure the mission
// holds for the operator. The body names the mission whose hold the operator saw, and nothing else; the answer is a
// receipt, and whether an attempt started is read back from the mission record.

import { describe, expect, it } from 'vitest';
import { readBoot } from './boot';
import { HttpSource } from './http';
import type { FetchLike } from './http';

const BOOT_JSON = JSON.stringify({
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-session-1',
  credential: 'not-a-real-credential-value',
  credential_header: 'X-Console-Credential',
  request_header: 'X-Console-Request',
});

function bootDocument(text: string) {
  return { getElementById: (id: string) => (id === 'kc-console-boot' ? { textContent: text } : null) };
}

function fetchAnswering(status: number, body: unknown) {
  const calls: { url: string; method: string; body: string | null; headers: Record<string, string> }[] = [];
  const impl: FetchLike = async (url, init) => {
    calls.push({
      url,
      method: init?.method ?? 'GET',
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: typeof init?.body === 'string' ? init.body : null,
    });
    return { ok: status >= 200 && status < 300, status, json: async () => body } as unknown as Response;
  };
  return { impl, calls };
}

describe('the mission retry over HTTP', () => {
  it('posts the held mission id to /api/mission/retry with the guard headers', async () => {
    const { impl, calls } = fetchAnswering(200, { ok: true, mission_id: 'mission-0001' });
    const source = new HttpSource({ fetchImpl: impl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
    const outcome = await source.missionRetry('mission-0001');
    expect(outcome).toEqual({ kind: 'started', mission_id: 'mission-0001' });
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual(['POST /api/mission/retry']);
    expect(JSON.parse(calls[0]?.body ?? 'null')).toEqual({ mission_id: 'mission-0001' });
    expect(calls[0]?.headers['X-Console-Credential']).toBe('not-a-real-credential-value');
  });

  it('reads a runtime without the route as a refusal, and a refusal with its own reason', async () => {
    const missing = new HttpSource({ fetchImpl: fetchAnswering(404, null).impl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
    const absent = await missing.missionRetry('mission-0001');
    expect(absent.kind).toBe('refused');
    const refusing = new HttpSource({
      fetchImpl: fetchAnswering(409, { ok: false, reason_code: 'mission_not_held_for_decision', detail: 'the mission is running' }).impl,
      eventSourceFactory: null,
      boot: readBoot(bootDocument(BOOT_JSON)),
    });
    expect(await refusing.missionRetry('mission-0001')).toEqual({ kind: 'refused', reason_code: 'mission_not_held_for_decision', detail: 'the mission is running' });
  });

  it('never calls a 5xx a refusal: the attempt may have started', async () => {
    const source = new HttpSource({ fetchImpl: fetchAnswering(502, null).impl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
    expect((await source.missionRetry('mission-0001')).kind).toBe('unknown');
  });
});
