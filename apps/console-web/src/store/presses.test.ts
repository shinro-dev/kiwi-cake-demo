// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// PRESS RECEIPTS ARE A LIST (spec rule 6.5, audit T2). On 2026-09-27 each receipt overwrote the one before, so a Stop
// refused after a Start took left no trace of the Start. Every press on this display is one entry, appended and never
// overwritten; a pending entry receives its own answer in place. The entries are this display's own record and carry
// this display's clock, labelled as such by the screen.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { appendPress, answerPress } from './presses';
import type { PressEntry } from './presses';
import { ConsoleStore } from './store';

const AT = '2026-09-27T14:30:02.000Z';

function entry(id: string, control: PressEntry['control']): PressEntry {
  return { id, control, pressed_at: AT, answer: 'pending', text: null, answered_at: null, mission_id: null, run_id: null, record: null };
}

describe('the press list, as a pure list', () => {
  it('keeps three presses as three entries, in the order they were pressed', () => {
    let list: PressEntry[] = [];
    list = appendPress(list, entry('p1', 'start'));
    list = appendPress(list, entry('p2', 'stop'));
    list = appendPress(list, entry('p3', 'stop'));
    expect(list.map((item) => `${item.id}:${item.control}`)).toEqual(['p1:start', 'p2:stop', 'p3:stop']);
  });

  it('gives a pending entry its answer in place and leaves every other entry as it was', () => {
    let list = appendPress(appendPress([], entry('p1', 'start')), entry('p2', 'stop'));
    list = answerPress(list, 'p1', { answer: 'taken', text: 'The runtime wrote down mission m-1.', answered_at: AT, record: 'mission:m-1:started' });
    expect(list[0]).toMatchObject({ id: 'p1', answer: 'taken', text: 'The runtime wrote down mission m-1.', record: 'mission:m-1:started' });
    expect(list[1]).toEqual(entry('p2', 'stop'));
    // An answer is given once: a second answer to the same entry changes nothing.
    const again = answerPress(list, 'p1', { answer: 'refused', text: 'late', answered_at: AT, record: null });
    expect(again[0]?.answer).toBe('taken');
  });
});

describe('the press list, kept by the store', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:30:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('appends Start, then two Stops, each with its own answer, none overwriting another', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }));
    await store.start();
    await store.startMission();
    await vi.advanceTimersByTimeAsync(1000);
    await store.requestStop();
    await vi.advanceTimersByTimeAsync(3000);
    await store.requestStop();
    const presses = store.getSnapshot().presses;
    expect(presses.map((item) => item.control)).toEqual(['start', 'stop', 'stop']);
    expect(presses[0]?.answer).toBe('taken');
    expect(presses[0]?.text).toContain('synthetic-mission-0001');
    expect(presses[0]?.mission_id).toBe(null);
    expect(presses[1]?.answer).toBe('taken');
    expect(presses[2]?.answer).toBe('refused');
    expect(presses[2]?.text).toContain('Stop refused');
    for (const item of presses) expect(item.pressed_at).toMatch(/Z$/);
    store.dispose();
  });

  it('records a press refused at the click, before anything is sent', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }));
    await store.start();
    store.refusedPress('retry', 'This is not true at the click, so nothing was sent: no mission is on the record.');
    const presses = store.getSnapshot().presses;
    expect(presses).toHaveLength(1);
    expect(presses[0]).toMatchObject({ control: 'retry', answer: 'refused' });
    store.dispose();
  });

  it('records an activation press with the operation it answered as its record', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'one-block' }));
    await store.start();
    await store.startMission();
    for (let slice = 0; slice < 200 && store.getSnapshot().mission?.state !== 'WAITING_FOR_CAPABILITY'; slice += 1) await vi.advanceTimersByTimeAsync(250);
    await store.activateCandidate('mobile-demo-active-successor-abort-swap');
    const press = store.getSnapshot().presses.at(-1);
    expect(press?.control).toBe('candidate');
    expect(press?.answer).toBe('taken');
    expect(press?.record).toBe('activation:synthetic-operation-0001');
    expect(press?.mission_id).toBe('synthetic-mission-0001');
    store.dispose();
  });

  it("keeps the Start dialog's confirmations in the Start press, where the dialog says they are kept", async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }));
    await store.start();
    await store.startMission(['I am at the robot and the servo power switch is within reach.']);
    const press = store.getSnapshot().presses.at(-1);
    expect(press?.control).toBe('start');
    expect(press?.text).toContain('Confirmed on this display: I am at the robot and the servo power switch is within reach.');
    store.dispose();
  });
});
