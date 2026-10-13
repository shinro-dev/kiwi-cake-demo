// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE RECONNECT THIS CONSOLE EXISTS FOR: a tab that was left open while the runtime was replaced.
//
// The runtime answers a cursor it cannot serve at all with a resync line that carries NO missing range and the reason
// `cursor_ahead` (demo_runtime.journal.EventLog.subscribe): the cursor belongs to a log it is not serving any more.
// This client used to require a two-element range on that line, so the line fell through to "malformed", the tab
// counted a parse failure, kept the cursor it could never be served from, and asked for it again on every reconnect.
// These tests pin the three things that had to become true instead: the line is read, the fold restarts from the new
// log, and the next request asks for something the runtime can actually answer.

import { describe, expect, it } from 'vitest';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { HttpSource } from './http';
import type { EventSourceLike, FetchLike } from './http';
import type { EventsMessage } from './types';
import { ConsoleStore } from '../store/store';

/** An EventSource this test drives by hand: nothing here opens a socket. */
class FakeStream implements EventSourceLike {
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {}

  close(): void {
    this.closed = true;
  }

  /** One line of the runtime's stream, exactly as the API forwards it. */
  push(line: unknown): void {
    this.onmessage?.({ data: JSON.stringify(line) });
  }

  /** The stream broke. The client reopens from the cursor it has reached. */
  fail(): void {
    this.onerror?.({});
  }
}

function streams(): { opened: FakeStream[]; factory: (url: string) => EventSourceLike } {
  const opened: FakeStream[] = [];
  return {
    opened,
    factory: (url: string) => {
      const stream = new FakeStream(url);
      opened.push(stream);
      return stream;
    },
  };
}

const CURSOR_AHEAD = { resync: { retained_from_seq: 8, missed: null, reason: 'cursor_ahead' } };

/** Answers the reads `ConsoleStore.start` makes. No command path is exercised here. */
const fetchImpl: FetchLike = async (url: string) => {
  const body = url.includes('/hello')
    ? { ok: true, contract_version: '1', runtime_session: 'runtime-session-a', read_only: false, commands_enabled: true }
    : url.includes('/snapshot')
      ? runningSnapshot
      : url.includes('/history')
        ? { ok: true, runs: [] }
        : {};
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
};

describe('the resync line that carries no missing range', () => {
  it('is read as a resync, and never counted as a line that could not be read', () => {
    const { opened, factory } = streams();
    const source = new HttpSource({ fetchImpl, eventSourceFactory: factory });
    const seen: EventsMessage[] = [];
    const stop = source.subscribeEvents(500, (message) => seen.push(message), () => undefined);
    opened[0]?.push(CURSOR_AHEAD);
    stop();
    expect(seen.length).toBe(1);
    const first = seen[0];
    expect(first !== undefined && 'resync' in first).toBe(true);
    if (first !== undefined && 'resync' in first) {
      expect(first.resync.missed).toBeNull();
      expect(first.resync.reason).toBe('cursor_ahead');
      expect(first.resync.retained_from_seq).toBe(8);
    }
  });

  it('still refuses a resync line that says nothing this console can act on', () => {
    const { opened, factory } = streams();
    const source = new HttpSource({ fetchImpl, eventSourceFactory: factory });
    const seen: EventsMessage[] = [];
    const stop = source.subscribeEvents(0, (message) => seen.push(message), () => undefined);
    opened[0]?.push({ resync: { missed: null } });
    opened[0]?.push({ resync: { retained_from_seq: 4, missed: [7] } });
    stop();
    expect(seen.every((message) => 'malformed' in message)).toBe(true);
    expect(seen.length).toBe(2);
  });

  it('asks the next stream for a cursor the runtime can serve, instead of the one it just refused', async () => {
    const { opened, factory } = streams();
    const source = new HttpSource({ fetchImpl, eventSourceFactory: factory, reconnectDelayMs: 0 });
    const stop = source.subscribeEvents(500, () => undefined, () => undefined);
    expect(opened[0]?.url).toContain('since=500');
    opened[0]?.push(CURSOR_AHEAD);
    opened[0]?.fail();
    await new Promise((resolve) => setTimeout(resolve, 5));
    // The oldest seq the runtime said it can still serve, minus one: the next record it sends is the first one this
    // tab has not seen. Asking for 500 again would be answered with the same refusal for as long as the tab is open.
    expect(opened[1]?.url).toContain('since=7');
    stop();
  });

  it('keeps moving the cursor FORWARD on a resync that does carry a range', async () => {
    const { opened, factory } = streams();
    const source = new HttpSource({ fetchImpl, eventSourceFactory: factory, reconnectDelayMs: 0 });
    const stop = source.subscribeEvents(10, () => undefined, () => undefined);
    opened[0]?.push({ resync: { retained_from_seq: 41, missed: [11, 40] } });
    opened[0]?.fail();
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(opened[1]?.url).toContain('since=40');
    stop();
  });
});

describe('a tab whose runtime was replaced under it', () => {
  it('resynchronizes the whole view instead of counting parse failures', async () => {
    const { opened, factory } = streams();
    const store = new ConsoleStore(new HttpSource({ fetchImpl, eventSourceFactory: factory, reconnectDelayMs: 0 }));
    await store.start();
    expect(store.getSnapshot().view.seq_cursor).toBe(19);
    opened[0]?.push(CURSOR_AHEAD);
    const view = store.getSnapshot().view;
    expect(view.counters.malformed_lines).toBe(0);
    expect(view.counters.cursor_replacements).toBe(1);
    expect(view.seq_cursor).toBe(7);
    expect(view.machine).toBeNull();
    // The gap is DECLARED: the runtime said what it could not serve, and the screen shows a gap that really exists.
    expect(view.stream_gaps).toEqual([{ from_seq: 1, to_seq: 7, declared: true }]);
    store.dispose();
  });
});
