// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AN ENDING IS ACKNOWLEDGED ONLY AFTER IT HAPPENED (redesign verdict item 13). The in-page rehearsal numbers its mission
// and runs afresh on every load, so a second rehearsal ended with the key of the first; its card was born collapsed,
// "Mission complete at 20:17:14Z. Acknowledged on this display at 20:16:47Z", an acknowledgement older than the ending
// it claims to acknowledge. The key now carries the mission's start, and an acknowledgement older than the mission's
// start never collapses its card.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import { ackKeyOf } from '../model/outcomeCard';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { settle } from './demoScenarioPlay';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

/** One rehearsal of far-block-success on a tab of its own, played to its end, with this display's storage. */
async function rehearse(storage: { getItem: (key: string) => string | null; setItem: (key: string, value: string) => void }): Promise<ConsoleStore> {
  const store = new ConsoleStore(new SyntheticSource({ scenario: scenarioFrom(scenarioUrl('far-block-success')) }), { storage });
  await store.start();
  await settle(store);
  await pressStart(store);
  for (let slice = 0; slice < 2400; slice += 1) {
    await vi.advanceTimersByTimeAsync(250);
    const snap = store.getSnapshot();
    if (snap.mission?.state === 'COMPLETE' && snap.view.active_run === null && slice > 20) break;
  }
  await vi.advanceTimersByTimeAsync(6000);
  await settle(store);
  return store;
}

describe('an ending is acknowledged only after it happened', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T20:10:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('a second rehearsal on this display ends expanded, although the first one was acknowledged', async () => {
    const stored: Record<string, string> = {};
    const storage = { getItem: (key: string) => stored[key] ?? null, setItem: (key: string, value: string) => void (stored[key] = value) };
    const first = await rehearse(storage);
    expect(render(first)).toContain('data-outcome-card="expanded"');
    const firstMission = first.getSnapshot().mission;
    const key = ackKeyOf(firstMission);
    expect(key).not.toBeNull();
    first.acknowledge({ key: key ?? '', mission_id: firstMission?.mission_id ?? '', run_id: null, attempt: null, verdict: 'complete' });
    expect(render(first)).toContain('data-outcome-card="collapsed"');
    first.dispose();

    // A reload a minute later: the in-page runtime starts again and numbers its mission as the first one was numbered.
    await vi.advanceTimersByTimeAsync(60_000);
    const second = await rehearse(storage);
    const secondMission = second.getSnapshot().mission;
    expect(secondMission?.mission_id).toBe(firstMission?.mission_id);
    expect(ackKeyOf(secondMission)).not.toBe(key);
    const markup = render(second);
    second.dispose();
    expect(markup).toContain('data-outcome-card="expanded"');
    expect(markup).not.toContain('Acknowledged on this display');
  }, 180_000);
});
