// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// STOP FROM THE MOMENT A COMMAND IS SENT UNTIL THE RECORD SAYS NOTHING RUNS (verdict 2026-09-27 must_fix 2; safety).
//
// A standalone Scan scene, Observe or Return arm is a run of its own, and the arm moves in it. The runtime publishes the
// run before it answers the command (supervisor._accept_and_spawn, which also writes run.started before the answer) and
// serves Stop enabled while it is active, but this page read that only from the next 5 s snapshot: for up to one poll
// Stop was off with "there is no run to stop" while the record already showed the run, and Scan scene was still offered
// beside it. Sampled every 250 ms here: whenever the record shows a run in progress, Stop is on and no motion control is
// offered; Stop is on the moment the command's answer is in; and it stays on while no snapshot read after that answer
// has answered, however the stream and the reads fail.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, EventsMessage } from '../api/types';
import type { SyntheticScenario } from '../panels/sourceChoice';
import { runMayBeActive, stopOffReason } from '../model/failureBanner';
import type { MissionRecord } from '../model/missionRecord';
import { initialState } from '../model/reducer';
import { runIsActive } from '../model/steps';
import { scanSceneCommand } from '../panels/Actions';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { readScreen } from './demoScenarioPlay';

const MOTION = ['start', 'scan_scene', 'observe', 'return_arm', 'retry'];

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function tag(markup: string, control: string): string {
  return new RegExp(`<button[^>]*data-control="${control}"[^>]*>`).exec(markup)?.[0] ?? '(not drawn)';
}

const CASES: { scenario: SyntheticScenario; label: string; command: Command }[] = [
  { scenario: 'scene-scan', label: 'Scan scene', command: scanSceneCommand() },
  { scenario: 'observe-landmarks-only', label: 'Observe', command: { type: 'observe', args: { holding: false } } as Command },
  { scenario: 'far-block-success', label: 'Return arm', command: { type: 'return_arm', args: { plan_only: false } } as Command },
];

describe('Stop while a standalone command runs', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  for (const item of CASES) {
    it(`${item.label}: Stop is on and no motion control is offered for as long as the record shows the run`, async () => {
      const store = new ConsoleStore(new SyntheticSource({ scenario: item.scenario }), { storage: null });
      await store.start();
      await vi.advanceTimersByTimeAsync(1234);
      const before = readScreen(render(store), store);
      // The pre-state the defect lived in: the runtime's list says there is no run to stop.
      expect(before.off['stop']).toBe(true);
      const track = await store.submit(item.command);
      expect(track.result?.status).not.toBe('rejected');
      expect(track.result?.run_id ?? null).not.toBeNull();

      // THE MOMENT THE ANSWER IS IN: the run is the runtime's, and Stop is on.
      const taken = readScreen(render(store), store);
      expect(taken.off['stop']).toBe(false);
      expect(taken.offered.filter((id) => MOTION.includes(id))).toEqual([]);

      let activeSamples = 0;
      for (let slice = 0; slice < 40; slice += 1) {
        await vi.advanceTimersByTimeAsync(250);
        const snap = store.getSnapshot();
        const screen = readScreen(render(store), store);
        if (!runIsActive(snap.view)) continue;
        activeSamples += 1;
        expect({ at: (slice + 1) * 250, stopOff: screen.off['stop'] }).toEqual({ at: (slice + 1) * 250, stopOff: false });
        expect(screen.offered.filter((id) => MOTION.includes(id))).toEqual([]);
      }
      expect(activeSamples).toBeGreaterThan(0);
      store.dispose();
    });
  }

  it('a run another display started: Stop is on as soon as the stream shows it, before the next snapshot does', async () => {
    const source = new SyntheticSource({ scenario: 'scene-scan' });
    // THE WATCHING DISPLAY'S SNAPSHOT READS ANSWER AFTER 600 MS, as a read over HTTP takes time. The stream's run.started
    // now asks for a snapshot at once (verify-r1 must_fix 5), and an in-page source answers it before the next sample, so
    // without this the window this test exercises (the stream showing the run before a snapshot does) would not occur
    // here although it does on a live runtime. The assertions are unchanged.
    const slow = { value: false };
    const slowSnapshot = new Proxy(source, {
      get(target, property, receiver) {
        if (property !== 'snapshot') return Reflect.get(target, property, receiver) as unknown;
        return async () => {
          const answer = await target.snapshot();
          if (slow.value) await new Promise((resolve) => setTimeout(resolve, 600));
          return answer;
        };
      },
    });
    const watching = new ConsoleStore(slowSnapshot, { storage: null });
    const other = new ConsoleStore(source, { storage: null });
    await watching.start();
    await other.start();
    slow.value = true;
    await vi.advanceTimersByTimeAsync(1234);
    const track = await other.submit(scanSceneCommand());
    expect(track.result?.status).not.toBe('rejected');

    let streamAhead = 0;
    for (let slice = 0; slice < 40; slice += 1) {
      await vi.advanceTimersByTimeAsync(250);
      const snap = watching.getSnapshot();
      if (!runMayBeActive(snap.view)) continue;
      const screen = readScreen(render(watching), watching);
      // The samples where the stream shows the run and this display's snapshot does not yet.
      if (snap.view.active_run === null) streamAhead += 1;
      expect({ at: (slice + 1) * 250, stopOff: screen.off['stop'] }).toEqual({ at: (slice + 1) * 250, stopOff: false });
      expect(screen.offered.filter((id) => MOTION.includes(id))).toEqual([]);
    }
    expect(streamAhead).toBeGreaterThan(0);
    watching.dispose();
    other.dispose();
  });
});

/**
 * The synthetic source, whose reads throw and whose event stream goes quiet from the moment a command's answer has been
 * handed back (`cut`), until `down.value` is cleared: the runtime took the command and then stopped answering.
 */
function cutAfterAnswer(down: { value: boolean }): ConsoleSource {
  const source = new SyntheticSource({ scenario: 'scene-scan' });
  const reads = new Set(['snapshot', 'mission', 'robotStatus', 'history', 'cakeStatus', 'capabilityCheck', 'signatureCheck', 'missionCandidates']);
  return new Proxy(source, {
    get(target, property, receiver) {
      if (property === 'submit') {
        return async (commandId: string, command: Command) => {
          const answer = await target.submit(commandId, command);
          down.value = true;
          return answer;
        };
      }
      if (typeof property === 'string' && reads.has(property)) {
        const read = Reflect.get(target, property, receiver) as () => Promise<unknown>;
        return async () => {
          if (down.value) throw new Error('the runtime did not answer');
          return read.call(target);
        };
      }
      if (property === 'subscribeEvents') {
        return (cursor: number, onMessage: (message: EventsMessage) => void) =>
          target.subscribeEvents(cursor, (message) => {
            if (!down.value) onMessage(message);
          });
      }
      const value = Reflect.get(target, property, receiver) as unknown;
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  }) as ConsoleSource;
}

describe('Stop after a command whose following snapshot read does not answer', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('stays on, with no motion control offered, until a snapshot read sent after the answer answers', async () => {
    const down = { value: false };
    const store = new ConsoleStore(cutAfterAnswer(down), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1234);
    expect(readScreen(render(store), store).off['stop']).toBe(true);

    const track = await store.submit(scanSceneCommand());
    expect(track.result?.status).not.toBe('rejected');
    // The runtime took it and then answered nothing: no snapshot, no stream. The list on the screen is from before the run.
    expect(store.getSnapshot().view.active_run).toBeNull();
    expect(store.getSnapshot().commandInFlight).toBe(true);

    for (let slice = 0; slice < 8; slice += 1) {
      await vi.advanceTimersByTimeAsync(2500);
      const markup = render(store);
      const screen = readScreen(markup, store);
      expect({ at: (slice + 1) * 2500, stopOff: screen.off['stop'] }).toEqual({ at: (slice + 1) * 2500, stopOff: false });
      expect(markup).not.toMatch(/there is no run to stop/);
      expect(screen.offered.filter((id) => MOTION.includes(id))).toEqual([]);
    }

    // The runtime answers again: the list covers the run from here, and says what Stop is.
    down.value = false;
    await vi.advanceTimersByTimeAsync(5000);
    const snap = store.getSnapshot();
    expect(snap.commandInFlight).toBe(false);
    const stop = snap.view.capabilities.find((item) => item.id === 'stop');
    const screen = readScreen(render(store), store);
    expect(screen.off['stop']).toBe(stop?.state === 'enabled' || runMayBeActive(snap.view) ? false : true);
    store.dispose();
  });

  it('a refused command started nothing and leaves Stop as the list says', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'scene-scan' }), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1234);
    // A command the contract does not know is refused by the runtime and starts nothing.
    const track = await store.submit({ type: 'not_a_command', args: {} } as unknown as Command);
    expect(track.result?.status ?? 'rejected').toBe('rejected');
    expect(store.getSnapshot().commandInFlight).toBe(false);
    expect(tag(render(store), 'stop')).toContain('aria-disabled="true"');
    store.dispose();
  });
});

describe('stopOffReason over a list that says there is no run', () => {
  const noRun = {
    ...initialState(),
    capabilities: [{ id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', dependency: 'owner', planned: false }],
  } as ReturnType<typeof initialState>;

  it('is off with the runtime words when nothing on the record may be moving', () => {
    expect(stopOffReason(noRun, false, null)).toBe('there is no run to stop');
  });

  it('is on while a command from this tab is not settled', () => {
    expect(stopOffReason(noRun, false, null, true)).toBeNull();
  });

  it('is on while the stream shows a run.started with no end', () => {
    const started = { ...noRun, run_started: { command_id: 'c1', command_type: 'scan_scene', plan_only: false, holding: null, started_wall_utc: '2026-09-27T05:00:01Z', run_id: 'r1', seq: 7 } } as ReturnType<typeof initialState>;
    expect(stopOffReason(started, false, null)).toBeNull();
  });

  it('is on while the mission record shows a mission the runtime is carrying', () => {
    const running = { mission_id: 'm1', state: 'RUNNING', waiting_reason: null, hold: null } as unknown as MissionRecord;
    expect(stopOffReason(noRun, false, running)).toBeNull();
    const ended = { mission_id: 'm1', state: 'COMPLETE', waiting_reason: null, hold: null } as unknown as MissionRecord;
    expect(stopOffReason(noRun, false, ended)).toBe('there is no run to stop');
  });
});
