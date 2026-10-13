// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A TAB OPENED AFTER A COMMAND WITH NO PLACEMENT HAS ENDED (a reload, a second display opened for an audience).
//
// A scene scan, an Observe and a Return arm each end as the controller ends them: exit 0 and no stated outcome. The
// snapshot's last outcome says only that; which command it was is in the run's own `run.started` and in the history.
// A fresh tab folded no records, so until one of those is read it does not know the command. These pin that the
// screen reads the command from the run on the screen (its records, once read back) before the history, so the ended
// command is never drawn as a failure: not before the history read has answered, not when it fails, and not in the
// moment before the run's records are read back.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import type { HistoryResult } from '../api/types';
import { needsHydration, selectedRun } from '../model/selectedRun';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { readScreen } from './demoScenarioPlay';
import type { ScreenRead } from './demoScenarioPlay';

type HistoryRead = 'delayed' | 'failed';

function render(store: ConsoleStore): ScreenRead {
  return readScreen(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen))), store);
}

/** The same runtime, with its history read answered late or not at all; every other read is the source's own. */
function withHistory(source: SyntheticSource, read: HistoryRead): SyntheticSource {
  const history = async (): Promise<HistoryResult> => {
    if (read === 'failed') return { ok: false, reason_code: 'history_unreachable' } as HistoryResult;
    await new Promise((resolve) => setTimeout(resolve, 60_000));
    return source.history();
  };
  return new Proxy(source, {
    get(target, key) {
      if (key === 'history') return history;
      const value: unknown = Reflect.get(target, key, target);
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  });
}

/** Play `command` to its end on one tab, as the operator would, and return the runtime it ran on. */
async function ended(scenario: ScenarioName, command: Command): Promise<SyntheticSource> {
  const source = new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(scenario)) });
  const store = new ConsoleStore(source);
  await store.start();
  await store.submit(command);
  for (let slice = 0; slice < 400; slice += 1) {
    await vi.advanceTimersByTimeAsync(250);
    const view = store.getSnapshot().view;
    if (view.outcome.run_id !== null && view.active_run === null) break;
  }
  await vi.advanceTimersByTimeAsync(6000);
  store.dispose();
  return source;
}

interface Opened {
  beforeRecords: ScreenRead;
  afterRecords: ScreenRead;
  historyRead: boolean;
  ranCommand: string | null;
}

/** Open a fresh tab on `source`: render before the run's records are read back, then after. */
async function openTab(source: SyntheticSource, read: HistoryRead): Promise<Opened> {
  const tab = new ConsoleStore(withHistory(source, read));
  await tab.start();
  await vi.advanceTimersByTimeAsync(0);
  const beforeRecords = render(tab);
  const snap = tab.getSnapshot();
  const selected = selectedRun(snap.view, snap.session.runtime_replaced);
  expect(needsHydration(selected, snap.view, snap.hydrated), 'the fresh tab still has the run to read back').toBe(true);
  if (selected.run_id !== null) await tab.hydrateRun(selected.run_id);
  await vi.advanceTimersByTimeAsync(0);
  const afterRecords = render(tab);
  const after = tab.getSnapshot();
  const historyRead = after.history !== null && after.history.ok;
  const ranCommand = after.hydrated?.view.run_started?.command_type ?? null;
  tab.dispose();
  return { beforeRecords, afterRecords, historyRead, ranCommand };
}

const CASES: Array<{ what: string; scenario: ScenarioName; command: Command; type: string }> = [
  { what: 'a completed Scan scene', scenario: 'scene-scan', command: { type: 'scan_scene', args: { plan_only: false } } as unknown as Command, type: 'scan_scene' },
  { what: 'a completed Observe', scenario: 'observe-landmarks-only', command: { type: 'observe', args: { holding: false } } as unknown as Command, type: 'observe' },
  { what: 'a completed Return arm', scenario: 'scene-scan', command: { type: 'return_arm', args: {} } as unknown as Command, type: 'return_arm' },
];

describe('a tab opened after a command with no placement has ended', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  for (const item of CASES) {
    for (const read of ['delayed', 'failed'] as const) {
      it(`${item.what}, the history read ${read === 'delayed' ? 'not answered yet' : 'failed'}: no failure stated and no failed stage`, async () => {
        const source = await ended(item.scenario, item.command);
        const opened = await openTab(source, read);
        // The history has said nothing, so only the run on the screen can name the command.
        expect(opened.historyRead).toBe(false);
        expect(opened.ranCommand).toBe(item.type);
        for (const [when, screen] of [['before the run is read back', opened.beforeRecords], ['after', opened.afterRecords]] as const) {
          expect(screen.active, when).toBeNull();
          expect(screen.outcome.cause, `${when}: no failure stated`).toBeNull();
          for (const ended of ['failed', 'stopped_by_controller', 'refused', 'no_outcome']) expect(Object.values(screen.stages), `${when}: no stage ${ended}`).not.toContain(ended);
          expect(screen.text, when).not.toContain('the run ended without a stated outcome');
        }
      }, 60_000);
    }
  }
});
