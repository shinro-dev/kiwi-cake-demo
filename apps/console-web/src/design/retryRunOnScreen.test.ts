// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AFTER TRY AGAIN, THE CARD, THE STAGES AND THE TIMELINE NAME THE RUN THAT IS GOING ON (redesign r4 verify-r1 must_fix 5).
//
// Try again parks the arm (one run) and starts a fresh attempt (another). When the park ended and the fresh attempt
// started, the card said "Run 2 of 2 done" while the Stages drew Find active, and the timeline had no line for run 3, for
// about three seconds: the snapshot's active run was still the park until the next poll, and the mission record was read
// once after the retry and then only on the poll. The fold's own newer `run.started` is the run in progress, and the
// stream's run.started and run.finished re-read the mission and the snapshot.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { element, readScreen, settle, textOf } from './demoScenarioPlay';
import { pressRetry } from './retryPress';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

async function playUntil(store: ConsoleStore, until: (snap: ReturnType<ConsoleStore['getSnapshot']>) => boolean): Promise<void> {
  for (let slice = 0; slice < 2400; slice += 1) {
    await vi.advanceTimersByTimeAsync(250);
    await settle(store);
    if (until(store.getSnapshot())) return;
  }
  throw new Error('the scenario never reached the hold');
}

describe('after Try again', () => {
  afterEach(() => vi.useRealTimers());

  for (const name of ['hold-then-retry-placed', 'align-no-proposal'] as ScenarioName[]) {
    it(`${name}: the card never says a run is done while a Stage is drawn active, and names the run the source runs`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') });
      const source = new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(name)) });
      const store = new ConsoleStore(source, { storage: null });
      await store.start();
      await settle(store);
      await pressStart(store);
      await playUntil(store, (snap) => snap.mission?.state === 'WAITING_FOR_CAPABILITY' && snap.mission.waiting_reason === 'operator_decision');
      await vi.advanceTimersByTimeAsync(5500);
      await settle(store);
      expect((await pressRetry(store)).tone).toBe('taken');

      const contradictions: string[] = [];
      const firstSeenAt = new Map<string, number>();
      const lateTimeline: string[] = [];
      const t0 = Date.now();
      for (let step = 0; step < 120; step += 1) {
        await vi.advanceTimersByTimeAsync(100);
        await settle(store);
        const markup = render(store);
        const screen = readScreen(markup, store);
        const sourceRun = (await source.snapshot()).active_run?.run_id ?? null;
        const stagesRun = /data-stage-timeline="" title="the records of run ([^ ,"]+)/.exec(markup)?.[1] ?? null;
        const runLine = textOf(element(markup, 'data-run-line=') ?? '');
        const activeStage = Object.entries(screen.stages).find(([, status]) => status === 'active')?.[0] ?? null;
        const at = Date.now() - t0;
        if (activeStage !== null && /\b(done|parked|placed|failed|stopped)\b/.test(runLine)) contradictions.push(`+${String(at)} ms: "${runLine}" beside ${activeStage} active`);
        if (activeStage !== null && sourceRun !== null && (screen.outcome.runId !== sourceRun || stagesRun !== sourceRun)) {
          contradictions.push(`+${String(at)} ms: source runs ${sourceRun}, card names ${String(screen.outcome.runId)}, stages drawn for ${String(stagesRun)}, ${activeStage} active`);
        }
        if (sourceRun !== null && !firstSeenAt.has(sourceRun)) firstSeenAt.set(sourceRun, at);
        for (const [runId, seenAt] of firstSeenAt) {
          if (at - seenAt >= 500 && !screen.timeline.some((key) => key.includes(runId)) && !lateTimeline.includes(runId)) lateTimeline.push(runId);
        }
      }
      expect(contradictions).toEqual([]);
      expect(lateTimeline).toEqual([]);
      // The fresh attempt did start inside the window, so the samples above covered the moment that was wrong.
      expect(firstSeenAt.size).toBeGreaterThan(0);
      store.dispose();
    }, 120_000);
  }
});
