// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE STEPS THE RUNTIME LISTS BEFORE A NEW MISSION, ON THE DEMO SCREEN'S OUTCOME CARD.
//
// After a mission ends, the runtime's `next_actions` is what the operator does by hand or by command, in its order:
// open the gripper by hand when a cube may be held, return the base when it is ahead, return the arm when it may have
// moved, then start (`mission.next_actions`). The card lists exactly those steps, in that order (spec rule 6.2.4), the
// ones done by hand tagged "by hand" and the ones with a control on this screen beside it; none is dropped.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { MissionResult } from '../api/types';
import { parseMission } from '../model/missionRecord';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { element, textOf } from './demoScenarioPlay';

class ServedMission extends SyntheticSource {
  constructor(private readonly served: Record<string, unknown>) {
    super({ scenario: 'hold-then-retry-placed' });
  }

  override async mission(): Promise<MissionResult> {
    const mission = parseMission(this.served);
    return mission === null ? { ok: false, reason_code: 'unparsed' } : { ok: true, mission };
  }
}

async function screenFor(nextActions: string[]): Promise<string> {
  const store = new ConsoleStore(new ServedMission({ mission_id: 'mission-1', state: 'STOPPED', stop_requested: true, targets: [], next_actions: nextActions }));
  await store.start();
  await vi.advanceTimersByTimeAsync(0);
  const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
  store.dispose();
  return markup;
}

describe('the steps the runtime lists before a new mission', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('says the by-hand steps the runtime lists after an end, in its order, before Start', async () => {
    const markup = await screenFor(['open_gripper_by_hand', 'return_base', 'return_arm', 'start']);
    const steps = element(markup, 'data-steps=""');
    expect(steps).not.toBeNull();
    expect([...(steps ?? '').matchAll(/data-step="([a-z_]+)"/g)].map((match) => match[1])).toEqual(['open_gripper_by_hand', 'return_base', 'return_arm', 'start']);
    const words = textOf(steps ?? '');
    const gripper = words.indexOf('Open the gripper by hand');
    const base = words.indexOf('Move the base back to its start by hand');
    const arm = words.indexOf('Return arm');
    const start = words.indexOf('Start a new mission');
    expect(gripper).toBeGreaterThanOrEqual(0);
    expect(base).toBeGreaterThan(gripper);
    expect(arm).toBeGreaterThan(base);
    expect(start).toBeGreaterThan(arm);
    expect((steps ?? '').match(/data-by-hand=""/g)?.length).toBe(2);
  });

  it('tags no step by hand when the runtime lists none', async () => {
    const markup = await screenFor(['return_arm', 'start']);
    expect(element(markup, 'data-steps=""')).not.toBeNull();
    expect(markup).not.toContain('data-by-hand');
  });
});
