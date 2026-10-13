// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A RELOAD REPRODUCES THE SCREEN FROM THE RECORD (docs/autonomous-demo/console-redesign-2026-09-27.md, the reload bar;
// redesign verdict 2026-09-27 items 5, 6, 7 and 8).
//
// A tab that watched a scenario and a tab opened afresh on the same runtime (a reload, a second display) must draw the
// same mission card, stages, scene, Cake region and record lines of the timeline, mid-run and at the end. The presses
// are this display's own record and are left out of the comparison. And on the watching tab, the mission card and the
// stages name one run at every moment: they read one answer for "the current run".

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { element, readScreen, settle, textOf } from './demoScenarioPlay';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function region(markup: string, name: string): string {
  return textOf(element(markup, `data-region="${name}"`) ?? '');
}

/** The timeline's keys that come from the record: every line but this display's presses. */
function recordKeys(markup: string): string[] {
  return [...markup.matchAll(/data-timeline-entry="([a-z_]+)" data-key="([^"]+)"/g)].filter((match) => match[1] !== 'press').map((match) => match[2] as string);
}

/** The run the stages are drawn for: the newest run their basis names. */
function stagesRun(markup: string): string | null {
  return /data-stage-timeline="" title="the records of run ([^ ,"]+)/.exec(markup)?.[1] ?? null;
}

/** The regions a reload must reproduce, as text, and the record lines of the timeline. */
function screenOf(markup: string): Record<string, string | string[]> {
  return {
    mission: region(markup, 'mission'),
    stages: region(markup, 'stages'),
    scene: region(markup, 'scene'),
    cake: region(markup, 'cake'),
    timeline: recordKeys(markup),
  };
}

function source(name: ScenarioName): SyntheticSource {
  return new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(name)) });
}

/** What the store's fold says about the run in progress: everything the card, the stages, the scene and Evidence read. */
function runState(store: ConsoleStore): Record<string, unknown> {
  const view = store.getSnapshot().view;
  const runId = view.run_started?.run_id ?? null;
  return {
    machine: view.machine,
    current_action: view.current_action,
    action_history: view.action_history,
    decision_frame: view.decision_frame,
    predicates: view.predicates,
    observations: view.observations,
    measured_state: view.measured_state,
    run_config: view.run_config,
    run_started: view.run_started,
    stop: view.stop,
    outcome: view.outcome,
    run_events: view.recent_events.filter((event) => event.run_id === runId).map((event) => event.seq),
  };
}

/** A store opened afresh on `on` and settled, for its fold; the caller disposes of it. */
async function openStore(on: SyntheticSource): Promise<ConsoleStore> {
  const tab = new ConsoleStore(on, { storage: null });
  await tab.start();
  await settle(tab);
  await vi.advanceTimersByTimeAsync(0);
  await settle(tab);
  return tab;
}

/** A tab opened afresh on `on`, settled as the screen's own effects settle it, rendered, and closed. */
async function reopen(on: SyntheticSource): Promise<string> {
  const tab = new ConsoleStore(on, { storage: null });
  await tab.start();
  await settle(tab);
  await vi.advanceTimersByTimeAsync(0);
  await settle(tab);
  const markup = render(tab);
  tab.dispose();
  return markup;
}

const ENDED = new Set(['COMPLETE', 'FAILED', 'STOPPED']);

/** Play swap-then-stopped-holding as its operator did, calling `each` with the playing tab's render at every slice. */
async function playSwap(on: SyntheticSource, store: ConsoleStore, each: (markup: string) => Promise<void>): Promise<void> {
  await store.start();
  await settle(store);
  await pressStart(store);
  let activated = false;
  for (let slice = 0; slice < 2400; slice += 1) {
    await vi.advanceTimersByTimeAsync(250);
    const snap = store.getSnapshot();
    if (!activated && snap.mission?.state === 'WAITING_FOR_CAPABILITY' && snap.mission.waiting_reason === 'capability_unavailable') {
      await vi.advanceTimersByTimeAsync(5500);
      await store.activateCandidate('mobile-demo-active-successor-abort-swap');
      await vi.advanceTimersByTimeAsync(300);
      await store.activateCandidate('mobile-demo-swapped');
      activated = true;
    }
    await settle(store);
    await each(render(store));
    const now = store.getSnapshot();
    if (ENDED.has(now.mission?.state ?? '') && now.view.active_run === null && slice > 40) break;
  }
  expect(activated, 'the scenario held for the capability and both Plans were pressed').toBe(true);
  void on;
}

describe('a reload reproduces the screen from the record', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('item 5: a tab opened mid-run draws the running run from the records the runtime serves', async () => {
    const on = source('swap-then-stopped-holding');
    const store = new ConsoleStore(on, { storage: null });
    let checked = 0;
    await playSwap(on, store, async (markup) => {
      // Only while the runtime still runs it: after its end the fresh tab reads a newer mission record than the watching
      // tab's last poll, which is a fresher read and not a difference of reload.
      if (store.getSnapshot().view.active_run === null || !on.running) return;
      const runLine = textOf(element(markup, 'data-run-line=') ?? '');
      if (!/STRADDLING|GRASP_CLOSE|ALIGN|PREGRASP/.test(runLine)) return;
      const reloaded = await reopen(on);
      expect(recordKeys(reloaded), 'the same record lines as the tab that watched').toEqual(recordKeys(markup));
      const reloadedLine = textOf(element(reloaded, 'data-run-line=') ?? '');
      expect(reloadedLine, 'the run line carries the state').toBe(runLine);
      expect(region(reloaded, 'stages'), 'the stages are read from the run').not.toContain('Not seen from here');
      expect(region(reloaded, 'stages')).toBe(region(markup, 'stages'));
      checked += 1;
    });
    store.dispose();
    expect(checked, 'a tab was opened while run 2 was in progress').toBeGreaterThan(3);
  }, 180_000);
  it('item 6: at every moment the mission card and the stages name the same run', async () => {
    const on = source('swap-then-stopped-holding');
    const store = new ConsoleStore(on, { storage: null });
    const pairs: string[] = [];
    await playSwap(on, store, async (markup) => {
      const screen = readScreen(markup, store);
      const card = screen.outcome.runId;
      const stages = stagesRun(markup);
      if (card === null || stages === null) return;
      pairs.push(`${card} / ${stages}`);
      expect(stages, `the stages are drawn for the run the card names (${String(pairs.length)} samples)`).toBe(card);
      // And they say the same of it: the stage the card's running run is in is the stage drawn in progress.
      const stage = screen.outcome.runStage;
      if (/ running, /.test(textOf(element(markup, 'data-run-line=') ?? '')) && stage !== null && stage !== 'none') {
        expect(screen.stages[stage as keyof typeof screen.stages], `${card} in ${stage}`).toBe('active');
      }
    });
    store.dispose();
    expect(pairs.some((pair) => pair.startsWith('synthetic-run-0002')), 'run 2 was sampled').toBe(true);
  }, 180_000);
  it('items 5 and 7: every region is the same on a tab opened mid-run, at several moments of the mission', async () => {
    const on = source('swap-then-stopped-holding');
    const store = new ConsoleStore(on, { storage: null });
    let opened = 0;
    let slice = 0;
    await playSwap(on, store, async (markup) => {
      slice += 1;
      if (store.getSnapshot().view.active_run === null || !on.running || slice % 6 !== 0) return;
      expect(screenOf(await reopen(on)), `a tab opened at slice ${String(slice)}`).toEqual(screenOf(markup));
      opened += 1;
    });
    store.dispose();
    expect(opened).toBeGreaterThan(2);
  }, 180_000);

  it('item 7: at the end, a tab opened 60 s later draws the same Cake region, scene, card, stages and record lines', async () => {
    const on = source('swap-then-stopped-holding');
    const store = new ConsoleStore(on, { storage: null });
    await playSwap(on, store, async () => undefined);
    await vi.advanceTimersByTimeAsync(6000);
    await settle(store);
    const end = render(store);
    await vi.advanceTimersByTimeAsync(60_000);
    await settle(store);
    const again = render(store);
    const reloaded = await reopen(on);
    store.dispose();
    expect(screenOf(again), 'the watching tab 60 s later').toEqual(screenOf(end));
    expect(screenOf(reloaded), 'a tab opened 60 s later').toEqual(screenOf(end));
    // The live slot's time is a record's, never the first reading this page happened to make.
    expect(region(end, 'cake')).not.toMatch(/since \d\d:\d\d:\d\dZ/);
    expect(region(end, 'cake')).toContain("Live slot: mobile_approach, active, generation 2, capsule a7a7a7a7a7a7, as the robot's reading says");
    expect(region(end, 'cake')).toContain('Set by the activation of mobile-demo-swapped at 14:16:10Z.');
    expect(region(end, 'scene')).toContain("the base advance uses mobile_approach, active at generation 2 (as the robot's reading says)");
  }, 180_000);

  it('item 8: Scan scene then Start: the Scene of a tab opened afresh is the playing tab\'s, and both name the runs drawn', async () => {
    const on = source('scene-scan');
    const store = new ConsoleStore(on, { storage: null });
    await store.start();
    await settle(store);
    await vi.advanceTimersByTimeAsync(1234);
    await store.submit({ type: 'scan_scene', args: { plan_only: false } } as unknown as Command);
    for (let slice = 0; slice < 400; slice += 1) {
      await vi.advanceTimersByTimeAsync(250);
      await settle(store);
      if (slice > 30 && store.getSnapshot().view.active_run === null && readScreen(render(store), store).offered.includes('start')) break;
    }
    await vi.advanceTimersByTimeAsync(6000);
    await settle(store);
    const scannedMarkup = render(store);
    const scanned = region(scannedMarkup, 'scene');
    const openedAfterScan = await reopen(on);
    expect(region(openedAfterScan, 'scene'), 'after the scan, before Start').toBe(scanned);
    expect(screenOf(openedAfterScan), 'every region, after the scan').toEqual(screenOf(scannedMarkup));
    expect(scanned).toMatch(/Drawn from the records of the Scan scene run synthetic-run-0001, started \d\d:\d\d:\d\dZ\./);
    const receipt = await pressStart(store);
    expect(receipt.tone).not.toBe('refused');
    let mid = 0;
    for (let slice = 0; slice < 2400; slice += 1) {
      await vi.advanceTimersByTimeAsync(250);
      await settle(store);
      const snap = store.getSnapshot();
      if (snap.view.active_run !== null && on.running && slice % 8 === 0) {
        const markup = render(store);
        const opened = await reopen(on);
        expect(region(opened, 'scene'), `a tab opened mid-run at slice ${String(slice)}`).toBe(region(markup, 'scene'));
        expect(screenOf(opened), `every region, a tab opened mid-run at slice ${String(slice)}`).toEqual(screenOf(markup));
        mid += 1;
      }
      if (ENDED.has(snap.mission?.state ?? '') && snap.view.active_run === null && slice > 20) break;
    }
    expect(mid, 'a tab was opened while the cycle ran').toBeGreaterThan(2);
    await vi.advanceTimersByTimeAsync(6000);
    await settle(store);
    const endMarkup = render(store);
    const reloadedMarkup = await reopen(on);
    const end = region(endMarkup, 'scene');
    const reloaded = region(reloadedMarkup, 'scene');
    store.dispose();
    expect(reloaded, 'a tab opened at the end').toBe(end);
    expect(screenOf(reloadedMarkup), 'every region, at the end').toEqual(screenOf(endMarkup));
    // It names the records it draws: the scan the operator pressed before Start, then the mission's run.
    expect(end).toMatch(/Drawn from the records of the Scan scene run synthetic-run-0001, started \d\d:\d\d:\d\dZ, and run synthetic-run-0002 \(Start\), started \d\d:\d\d:\d\dZ\./);
    expect(end).toContain('Placed at x -15.8 mm, y -100.0 mm');
  }, 180_000);

  it('item 5: the store rebuilt from the record folds the run in progress as the store that watched it', async () => {
    const on = source('swap-then-stopped-holding');
    const store = new ConsoleStore(on, { storage: null });
    let compared = 0;
    let slice = 0;
    await playSwap(on, store, async () => {
      slice += 1;
      if (store.getSnapshot().view.active_run === null || !on.running || slice % 5 !== 0) return;
      const tab = await openStore(on);
      expect(runState(tab), `a store opened at slice ${String(slice)}`).toEqual(runState(store));
      tab.dispose();
      compared += 1;
    });
    store.dispose();
    expect(compared).toBeGreaterThan(3);
  }, 180_000);
});
