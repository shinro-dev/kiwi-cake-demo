// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AT A MISSION'S NEXT RUN THE SCENE KEEPS WHAT THE EARLIER RUN SAW, AS THAT RUN'S (redesign r4 verify-r2 must_fix 3).
//
// The Scene folds the drawn runs' own records in run order, and a `run.started` dropped every block that was not the
// scan's. So at each new run of a mission (after the Cake swap, after Try again) the Scene said "Nothing seen yet" and
// "Nothing on this map yet" under its own line "Drawn from the records of run 1 ... and run 2", while run 1's records
// held the detected block and the timeline beside it listed run 1's TARGET_SELECTED: on the real record for about 34 s,
// right after the swap. The block now stays, drawn as the earlier run's ("seen by run <id> at HH:MM:SSZ"), until the new
// run reports a detection of its own, and the Scene says the new run has not reported what it sees yet.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { SyntheticSource } from '../api/synthetic';
import { buildFarBlockRun, SCENARIO_CATALOGUE, scenarioUrl } from '../api/syntheticScenarios';
import type { RunFactory, ScenarioName } from '../api/syntheticScenarios';
import { foldSceneMap } from '../model/sceneMap';
import { earlierWords, newestRunWaitingWords, SceneMap } from './SceneMap';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { element, settle, textOf } from './demoScenarioPlay';
import { pressRetry } from './retryPress';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

/** Every record this page holds for a run: the live fold's, a folded run's and a hydrated run's, the run's own only. */
function recordsOf(store: ConsoleStore, runId: string): ContractEvent[] {
  const snap = store.getSnapshot();
  const views = [snap.view, snap.runFolds[runId], snap.hydrated?.run_id === runId ? snap.hydrated.view : undefined];
  return views.flatMap((view) => view?.recent_events ?? []).filter((event) => event.run_id === runId);
}

function detects(events: readonly ContractEvent[]): boolean {
  return events.some(
    (event) =>
      event.kind === 'perception.result' &&
      ((event.payload.detector === 'cube-in-pad-frame' && event.payload.result === 'detected') || event.payload.detector === 'scene-block'),
  );
}

const EMPTY = /Nothing seen yet|Nothing on this map yet/;

describe("the Scene at a mission's next run", () => {
  afterEach(() => vi.useRealTimers());

  for (const name of ['one-block', 'hold-then-retry-placed'] as ScenarioName[]) {
    it(`${name}: never says nothing was seen while it names a run whose own records hold a detection`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-28T05:00:00Z') });
      const store = new ConsoleStore(new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(name)) }), { storage: null });
      await store.start();
      await settle(store);
      await pressStart(store);
      for (let i = 0; i < 1200; i += 1) {
        await vi.advanceTimersByTimeAsync(250);
        const mission = store.getSnapshot().mission;
        if (mission?.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason !== null) break;
      }
      await vi.advanceTimersByTimeAsync(5500);
      await settle(store);
      expect(textOf(element(render(store), 'data-region="scene"') ?? '')).not.toMatch(EMPTY);
      if (name === 'one-block') {
        for (const key of ['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped']) {
          await store.activateCandidate(key);
          await vi.advanceTimersByTimeAsync(50);
        }
      } else {
        await pressRetry(store);
      }
      const samples = name === 'one-block' ? 100 : 160; // 5 s after the swap, 8 s after Try again
      const bad: string[] = [];
      let namedTwo = false;
      for (let k = 0; k < samples; k += 1) {
        await vi.advanceTimersByTimeAsync(50);
        await settle(store);
        const markup = render(store);
        const scene = textOf(element(markup, 'data-region="scene"') ?? '');
        const named = [...textOf(element(markup, 'data-scene-records=""') ?? '').matchAll(/\b(synthetic-run-\d+)\b/g)].map((match) => match[1] as string);
        if (named.length > 1) namedTwo = true;
        const withDetection = named.filter((runId) => detects(recordsOf(store, runId)));
        if (withDetection.length > 0 && EMPTY.test(scene)) bad.push(`+${String((k + 1) * 50)} ms: "${scene.slice(0, 120)}" beside runs ${withDetection.join(', ')}`);
      }
      expect(namedTwo).toBe(true);
      expect(bad).toEqual([]);
      store.dispose();
    }, 120_000);
  }
});

function eventsOf(build: RunFactory, index: number, startSeq: number): ContractEvent[] {
  return build(index, startSeq, 1_000_000_000).steps.map((step) => ({ ...step.event, t_wall_utc: step.event.t_wall_utc ?? '2026-09-28T05:00:07.500Z' }) as ContractEvent);
}

function flowRun(name: string): RunFactory {
  const step = SCENARIO_CATALOGUE.find((spec) => spec.name === name)?.flow[0];
  if (step === undefined) throw new Error(`no run in ${name}`);
  return step.build;
}

function isDetection(event: ContractEvent): boolean {
  return event.kind === 'perception.result' && event.payload.detector === 'cube-in-pad-frame' && event.payload.result === 'detected';
}

describe("the Scene's fold across a mission's next run", () => {
  const first = eventsOf(buildFarBlockRun, 1, 0);
  const firstRun = first[0]?.run_id ?? '';
  const lastSeq = first[first.length - 1]?.seq ?? 0;
  const second = eventsOf(flowRun('not-placeable-budget'), 2, lastSeq);
  const secondRun = second[0]?.run_id ?? '';
  const detectedAt = second.findIndex(isDetection);

  it('keeps the block the earlier run saw, as that run\'s, until the new run reports a sighting of its own', () => {
    expect(detectedAt).toBeGreaterThan(0);
    const before = foldSceneMap([...first, ...second.slice(0, detectedAt)]);
    expect(before.blocks).toHaveLength(1);
    expect(before.blocks[0]).toMatchObject({ run_id: firstRun, earlier: true, selected: false, class: 'BASE_ADVANCE' });
    expect(before.newest_run).toBe(secondRun);
    const kept = before.blocks[0];
    if (kept === undefined) throw new Error('no block kept');
    expect(earlierWords(kept, null)).toMatch(new RegExp(`^seen by run ${firstRun}`));
    // "Has not reported what it sees yet" only before the new run's first cube-layer answer (verify-r3 must_fix 2). At the
    // cut above its record already holds the recognizer's detection, so the line says that and that it has not said where.
    const firstAnswer = second.findIndex((event) => event.kind === 'perception.result' && ['cube-recognizer', 'cube-in-pad-frame'].includes(event.payload.detector));
    expect(firstAnswer).toBeGreaterThan(0);
    expect(firstAnswer).toBeLessThan(detectedAt);
    expect(newestRunWaitingWords(foldSceneMap([...first, ...second.slice(0, firstAnswer)]))).toBe(`Run ${secondRun} has not reported what it sees yet; the block drawn is what run ${firstRun} saw.`);
    expect(newestRunWaitingWords(before, '2026-09-28')).toBe(
      `Run ${secondRun} saw a block at 05:00:07Z and has not reported where it is yet (cube-recognizer: detected); the block drawn is what run ${firstRun} saw.`,
    );
    // The new run's own records do not re-class the block the earlier run saw.
    expect(before.pulses).toEqual([]);
    expect(before.placed).toBeNull();

    const after = foldSceneMap([...first, ...second]);
    expect(after.blocks.map((block) => [block.class, block.run_id, block.earlier])).toEqual([['NOT_PLACEABLE', secondRun, false]]);
    expect(newestRunWaitingWords(after)).toBeNull();
  });

  it("never classes the earlier run's block with the new run's refusal", () => {
    const finished = second.find((event) => event.kind === 'run.finished');
    if (finished === undefined) throw new Error('no run.finished');
    const map = foldSceneMap([...first, ...second.slice(0, detectedAt), finished]);
    expect(map.blocks.map((block) => [block.class, block.run_id, block.earlier])).toEqual([['BASE_ADVANCE', firstRun, true]]);
  });

  it('draws the earlier run\'s block labelled as that run\'s, and never the empty words beside it', () => {
    const map = foldSceneMap([...first, ...second.slice(0, detectedAt)]);
    const text = textOf(renderToStaticMarkup(createElement(SceneMap, { map, title: 'Scene', drawnFrom: null, day: '2026-09-28' })));
    expect(text).not.toMatch(EMPTY);
    expect(text).toContain(`seen by run ${firstRun} at 05:00:07Z`);
    expect(text).toContain(`Run ${secondRun} saw a block at 05:00:07Z and has not reported where it is yet`);
  });
});
