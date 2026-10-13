// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

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

function sourceAnswering(status: number, body: unknown): HttpSource {
  const impl: FetchLike = async () => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;
  return new HttpSource({ fetchImpl: impl, eventSourceFactory: null, boot: readBoot(bootDocument(BOOT_JSON)) });
}

describe('the VLA status over HTTP', () => {
  it('a console started without the VLA door says the door is absent, not that the runner is down', async () => {
    expect(await sourceAnswering(404, { ok: false, reason_code: 'vla_not_configured' }).vlaStatus()).toEqual({ state: 'absent' });
  });

  it('a runner that does not answer reads unreachable', async () => {
    expect((await sourceAnswering(503, { ok: false, reason_code: 'vla_runner_unreachable' }).vlaStatus()).state).toBe('unreachable');
    expect((await sourceAnswering(200, { state: 'unreachable', reason_code: 'vla_runner_unreachable' }).vlaStatus()).state).toBe('unreachable');
  });

  it('the runner answer comes through whole', async () => {
    expect((await sourceAnswering(200, { state: 'ready', model_sha256: 'a'.repeat(64) }).vlaStatus()).state).toBe('ready');
  });
});
