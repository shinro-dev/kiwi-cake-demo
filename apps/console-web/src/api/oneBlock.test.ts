// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ONE-BLOCK DEMO, REHEARSED INSIDE THE PAGE. The synthetic source's `one-block` scenario plays the whole mission
// the operator will press on the robot (docs/autonomous-demo/one-block-demo-2026-09-26.md, section 6): Start, the
// search, the sweep, the block taken as the target with a measured BASE_ADVANCE, the run that ends before the pickup
// because it carries no capability, the mission held, an activation the resident aborts, one it observes, the same
// target resumed, the pick, the base pulses, the placement, the return and the mission complete. These pin that every
// beat is on the record the store reads, in the shapes the runtime sends, and that the scenario is never started by a
// timer: nothing plays until Start is pressed.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { SyntheticSource } from './synthetic';
import { scenarioFrom } from '../panels/sourceChoice';

function transitions(events: ContractEvent[]): string[] {
  return events.filter((event) => event.kind === 'state.transition').map((event) => (event.payload as { to_state: string }).to_state);
}

function diagnostics(events: ContractEvent[]): string[] {
  return events.filter((event) => event.kind === 'diagnostic.text').map((event) => (event.payload as { text: string }).text);
}

async function play(events: ContractEvent[], untilFinished: number): Promise<void> {
  // A run's steps are seconds apart; the fake clock is advanced in slices until the wanted run.finished has arrived.
  for (let slice = 0; slice < 400; slice += 1) {
    if (events.filter((event) => event.kind === 'run.finished').length >= untilFinished) return;
    await vi.advanceTimersByTimeAsync(250);
  }
  throw new Error('the scenario did not reach the wanted run.finished');
}

describe('the one-block scenario of the synthetic source', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('is chosen by the address, and only by it', () => {
    expect(scenarioFrom('?source=simulation&scenario=one-block')).toBe('one-block');
    expect(scenarioFrom('?source=simulation')).toBeNull();
    expect(scenarioFrom('?scenario=something-else')).toBeNull();
  });

  it('plays nothing until Start, and carries no mission until then', async () => {
    const source = new SyntheticSource({ scenario: 'one-block' });
    const events: ContractEvent[] = [];
    const off = source.subscribeEvents(0, (message) => {
      if ('event' in message) events.push(message.event);
    });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(events).toEqual([]);
    expect(await source.mission()).toEqual({ ok: false, reason_code: 'no_mission_recorded' });
    off();
  });

  it('plays the whole demo: search, sweep, selection, hold, abort, activation, resume, pick, pulses, place, return, complete', async () => {
    const source = new SyntheticSource({ scenario: 'one-block' });
    const events: ContractEvent[] = [];
    const off = source.subscribeEvents(0, (message) => {
      if ('event' in message) events.push(message.event);
    });

    const started = await source.missionStart();
    expect(started.kind).toBe('started');
    const running = await source.mission();
    if (!running.ok) throw new Error('no mission after Start');
    expect(running.mission.state).toBe('RUNNING');
    expect(running.mission.data_mode).toBe('synthetic');
    expect(running.mission.performs_motion).toBe(false);
    expect(running.mission.targets).toHaveLength(1);
    expect(running.mission.targets[0]?.requires_capability).toBe('auto');
    expect(running.mission.targets[0]?.measured_mode ?? null).toBeNull();

    // THE MEASURING RUN: no cube in the first frame, the sweep finds it, the block is selected and measures BASE_ADVANCE,
    // and the run ends before the pickup because it carries no capability.
    await play(events, 1);
    const first = transitions(events);
    expect(first.slice(0, 3)).toEqual(['SEARCH', 'SEARCH_SWEEP', 'SEARCH_SWEEP']);
    expect(first).toContain('TARGET_SELECTED');
    expect(first).not.toContain('PREGRASP');
    expect(diagnostics(events).some((line) => line.startsWith('[STATE] SEARCH_SWEEP {'))).toBe(true);
    expect(diagnostics(events).some((line) => line.startsWith('[STATE] TARGET_SELECTED {') && line.includes('"mode": "BASE_ADVANCE"'))).toBe(true);
    const measuring = events.find((event) => event.kind === 'run.finished');
    expect((measuring?.payload as { reason_code?: string }).reason_code).toBe('mission_capability_required');
    expect((measuring?.payload as { phase?: string }).phase).toBe('TARGET_SELECTED');

    const held = await source.mission();
    if (!held.ok) throw new Error('no mission while held');
    expect(held.mission.state).toBe('WAITING_FOR_CAPABILITY');
    expect(held.mission.waiting_reason).toBe('capability_unavailable');
    expect(held.mission.targets[0]?.state).toBe('WAITING_FOR_CAPABILITY');
    expect(held.mission.targets[0]?.measured_mode).toBe('BASE_ADVANCE');
    expect(held.mission.targets[0]?.runs).toHaveLength(1);
    const candidates = await source.missionCandidates();
    if (!candidates.ok) throw new Error('no candidates');
    expect(candidates.candidates.map((item) => item.key)).toEqual(['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped']);

    // THE ABORT-SWAP: the resident aborts the transaction, the slot is unchanged, the mission stays held.
    const aborted = await source.activateCandidate('mobile-demo-active-successor-abort-swap');
    expect(aborted.kind).toBe('answered');
    const afterAbort = await source.mission();
    if (!afterAbort.ok) throw new Error('no mission after the abort');
    expect(afterAbort.mission.state).toBe('WAITING_FOR_CAPABILITY');
    expect(afterAbort.mission.activations.map((item) => item.activation_state)).toEqual(['REJECTED_BY_CAKE']);
    expect(afterAbort.mission.activations[0]?.slot_after?.generation).toBe(afterAbort.mission.activations[0]?.slot_before?.generation);

    // THE SWAPPED PLAN: observed in the slot, the same target resumes, and the carrying run plays to the end.
    const observed = await source.activateCandidate('mobile-demo-swapped');
    expect(observed.kind).toBe('answered');
    const resumed = await source.mission();
    if (!resumed.ok) throw new Error('no mission after the activation');
    expect(resumed.mission.state).toBe('RUNNING');
    expect(resumed.mission.activations.map((item) => item.activation_state)).toEqual(['REJECTED_BY_CAKE', 'OBSERVED']);
    expect(resumed.mission.activations[1]?.slot_after?.capability_active).toBe(true);
    expect(resumed.mission.activations[1]?.slot_after?.generation).not.toBe(resumed.mission.activations[1]?.slot_before?.generation);

    await play(events, 2);
    const finished = events.filter((event) => event.kind === 'run.finished');
    expect((finished[1]?.payload as { outcome: string }).outcome).toBe('placed_new');
    const second = transitions(events).slice(first.length);
    for (const state of ['SEARCH', 'TARGET_SELECTED', 'PREGRASP', 'GRASP_CLOSE', 'OBJECT_SECURED', 'HOLD_OBSERVE', 'BASE_ADVANCE', 'PLACED', 'BASE_RETURN', 'BASE_RETURNED']) {
      expect(second, state).toContain(state);
    }
    expect(second.filter((state) => state === 'BASE_ADVANCE').length).toBeGreaterThanOrEqual(2);

    const complete = await source.mission();
    if (!complete.ok) throw new Error('no mission at the end');
    expect(complete.mission.state).toBe('COMPLETE');
    expect(complete.mission.targets[0]?.state).toBe('PLACED');
    expect(complete.mission.targets[0]?.runs).toHaveLength(2);
    // A second Start is a new mission on the same source, never a refusal for life.
    expect((await source.missionStart()).kind).toBe('started');
    off();
  });

  it('refuses an activation that is not for a held mission, and lets Stop end a held mission', async () => {
    const source = new SyntheticSource({ scenario: 'one-block' });
    const events: ContractEvent[] = [];
    const off = source.subscribeEvents(0, (message) => {
      if ('event' in message) events.push(message.event);
    });
    expect((await source.activateCandidate('mobile-demo-swapped')).kind).toBe('refused');
    await source.missionStart();
    expect((await source.missionStart()).kind).toBe('refused');
    await play(events, 1);
    const stop = await source.stop();
    expect(stop.outcome).toBe('taken');
    expect(stop.mission?.stopping).toBe(true);
    const stopped = await source.mission();
    if (!stopped.ok) throw new Error('no mission after the stop');
    expect(stopped.mission.state).toBe('STOPPED');
    expect(stopped.mission.targets[0]?.state).toBe('STOPPED');
    expect((await source.activateCandidate('mobile-demo-swapped')).kind).toBe('refused');
    off();
  });
});
