// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource } from '../api/types';
import type { VlaStatus } from '../model/vla';
import { ConsoleStore } from './store';

type VlaDoors = Partial<Pick<ConsoleSource, 'vlaStatus' | 'vlaCommand'>>;

function sourceWith(doors: VlaDoors): ConsoleSource {
  const base = new SyntheticSource();
  return Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base,
    { vlaStatus: undefined, vlaCommand: undefined }, doors);
}

describe('the VLA beat, from the store', () => {
  it('says whether the source offers the VLA door at all', () => {
    expect(new ConsoleStore(sourceWith({})).vlaOffered).toBe(false);
    expect(new ConsoleStore(sourceWith({ vlaStatus: async () => ({ state: 'standby' }) })).vlaOffered).toBe(true);
  });

  it('reads the runner state into the snapshot', async () => {
    const store = new ConsoleStore(sourceWith({ vlaStatus: async (): Promise<VlaStatus> => ({ state: 'ready', model_sha256: 'a'.repeat(64) }) }));
    expect(store.getSnapshot().vla).toBeNull();
    await store.refreshVla();
    expect(store.getSnapshot().vla?.state).toBe('ready');
  });

  it('sends one command, refuses a second press while one is in flight, then reads the state again', async () => {
    const sent: string[] = [];
    let release: () => void = () => undefined;
    let reads = 0;
    const store = new ConsoleStore(sourceWith({
      vlaStatus: async (): Promise<VlaStatus> => { reads += 1; return { state: 'loading' }; },
      vlaCommand: (op) => {
        sent.push(op);
        return new Promise((resolve) => { release = () => resolve({ ok: true, answer: { accepted: true } }); });
      },
    }));
    const first = store.vlaCommand('swap');
    expect(store.getSnapshot().vlaBusy).toBe(true);
    const second = await store.vlaCommand('run');
    expect(second.ok).toBe(false);
    expect(sent).toEqual(['swap']);
    release();
    expect((await first).ok).toBe(true);
    expect(store.getSnapshot().vlaBusy).toBe(false);
    expect(reads).toBeGreaterThan(0);
    expect(store.getSnapshot().vla?.state).toBe('loading');
  });

  it('refuses without sending on a source that has no VLA door', async () => {
    const store = new ConsoleStore(sourceWith({}));
    expect((await store.vlaCommand('swap')).ok).toBe(false);
  });

  it('keeps the runner refusal of the last press, and clears it when a press is taken', async () => {
    let answer: unknown = { accepted: false, reason: 'vla_runtime_busy', detail: 'a mission run is active' };
    const store = new ConsoleStore(sourceWith({
      vlaStatus: async (): Promise<VlaStatus> => ({ state: 'ready' }),
      vlaCommand: async () => ({ ok: true, answer }),
    }));
    await store.vlaCommand('run');
    expect(store.getSnapshot().vlaRefusal).toBe('vla_runtime_busy: a mission run is active');
    answer = { accepted: true };
    await store.vlaCommand('run');
    expect(store.getSnapshot().vlaRefusal).toBeNull();
  });

  it('says a press that did not reach the runner', async () => {
    const store = new ConsoleStore(sourceWith({
      vlaStatus: async (): Promise<VlaStatus> => ({ state: 'unreachable' }),
      vlaCommand: async () => ({ ok: false, answer: { ok: false, reason_code: 'vla_runner_unreachable' } }),
    }));
    await store.vlaCommand('swap');
    expect(store.getSnapshot().vlaRefusal).toBe('vla_runner_unreachable');
  });
  it('remembers that the runner was driving when it stops answering', async () => {
    let next: VlaStatus = { state: 'running' };
    const store = new ConsoleStore(sourceWith({ vlaStatus: async () => next }));
    await store.refreshVla();
    expect(store.getSnapshot().vlaMaybeDriving).toBe(true);
    next = { state: 'unreachable' };
    await store.refreshVla();
    expect(store.getSnapshot().vlaMaybeDriving).toBe(true);
    next = { state: 'ready' };
    await store.refreshVla();
    expect(store.getSnapshot().vlaMaybeDriving).toBe(false);
    next = { state: 'unreachable' };
    await store.refreshVla();
    expect(store.getSnapshot().vlaMaybeDriving).toBe(false);
  });

  it('Stop is sent even while another VLA command is in flight', async () => {
    const sent: string[] = [];
    let release: () => void = () => undefined;
    const store = new ConsoleStore(sourceWith({
      vlaStatus: async (): Promise<VlaStatus> => ({ state: 'running' }),
      vlaCommand: (op) => {
        sent.push(op);
        if (op === 'stop') return Promise.resolve({ ok: true, answer: { stopping: true } });
        return new Promise((resolve) => { release = () => resolve({ ok: true, answer: { accepted: true } }); });
      },
    }));
    const run = store.vlaCommand('run');
    expect((await store.vlaCommand('stop')).ok).toBe(true);
    expect(sent).toEqual(['run', 'stop']);
    release();
    await run;
  });
});
