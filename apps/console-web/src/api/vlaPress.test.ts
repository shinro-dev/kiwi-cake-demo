// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A VLA PRESS WAITS FOR THE RUNNER'S RUNTIME READS. The runner answers swap, run, carry and the pose moves only after
// reading the runtime's snapshot and mission state, which it reads twice when the first read times out (vla/runner.py
// RUNTIME_READ_TIMEOUT_S x RUNTIME_READ_ATTEMPTS, behind the console API's VlaForward.DEFAULT_TIMEOUT_S of 12 s). A tab
// that gave up at 6 s read a press the runner was still deciding as "no answer", and said so over a press that was taken.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpSource, VLA_ANSWER_TIMEOUT_MS } from './http';
import type { FetchLike } from './http';

const BOOT = {
  contract_version: '1',
  api_base: '/api',
  api_session: 'api-0001',
  credentials: { credential_header: 'x-console-credential', credential: 'a-per-launch-credential', request_header: 'x-console-request', request_value: '1' },
};

describe('a VLA press over the console door', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('waits longer than the console forward and the runner together need, then reads the press as not known', async () => {
    vi.useFakeTimers();
    const signals: Array<AbortSignal | undefined> = [];
    const fetchImpl: FetchLike = (_url, init) =>
      new Promise((_resolve, reject) => {
        signals.push(init?.signal ?? undefined);
        init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
      });
    expect(VLA_ANSWER_TIMEOUT_MS).toBeGreaterThanOrEqual(15_000);
    const pending = new HttpSource({ fetchImpl, eventSourceFactory: null, boot: BOOT }).vlaCommand('carry');
    let settled = false;
    void pending.then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(VLA_ANSWER_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const answer = await pending;
    expect(answer.ok).toBe(false);
    expect(String(answer.answer)).toContain('no answer to /vla/carry');
    expect(signals[0]?.aborted).toBe(true);
  });
});
