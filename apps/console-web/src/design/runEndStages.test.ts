// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AT A RUN'S END THE STAGES AND THE TOP BAR DO NOT SAY THE RUN IS GOING ON BESIDE A CARD THAT SAYS IT ENDED (redesign r4
// verify-r2 must_fix 2).
//
// The card reads the run in progress from the newer of the two records that name one (`activeRunOnRecord`): a run whose
// run.finished the fold holds is not in progress, however long the snapshot keeps naming it. The Stages read the
// snapshot's active run, and the readiness line printed the snapshot's `run_active` Start-off reason, so at every
// mission end and at the Cake hold a Stage said "In progress" and the top bar "Not ready: A run is already in
// progress." beside "Mission complete", "Mission failed", "Mission waiting for Cake" or "Run ended". Sampled every
// 100 ms from Start to rest, as the verifier did.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import type { Capability } from '@kiwi/demo-contracts';
import { enter, finished, started } from '../model/stageFixtures';
import { foldEvents, initialState } from '../model/reducer';
import { phaseProgress } from '../model/phase';
import type { ViewState } from '../model/types';
import { RUN_ENDED_NOT_IDLE, runEndedNotIdle } from '../panels/commandGuards';
import { presentationSteps } from './steps5';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { readScreen, settle } from './demoScenarioPlay';
import { pressRetry } from './retryPress';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

const ACTIVATIONS: Partial<Record<ScenarioName, string[]>> = {
  'one-block': ['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped'],
  'swap-then-stopped-holding': ['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped'],
};

/** The card's headline ids that say the run or the mission is not going on. */
const ENDED_HEADLINES = new Set(['run_ended_reading', 'complete', 'failed', 'waiting_cake', 'held_for_you', 'stopped_on_request']);

describe('the Stages and the readiness line beside a card that says the run ended', () => {
  afterEach(() => vi.useRealTimers());

  for (const name of ['one-block', 'far-block-success', 'hold-then-retry-placed', 'swap-then-stopped-holding'] as ScenarioName[]) {
    it(`${name}: no Stage is In progress and the top bar never says a run is already in progress`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-28T05:00:00Z') });
      const store = new ConsoleStore(new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(name)) }), { storage: null });
      await store.start();
      await settle(store);
      await pressStart(store);
      const t0 = Date.now();
      let activated = false;
      let retried = false;
      let sawEnd = false;
      let restAt = -1;
      const bad: string[] = [];
      for (let i = 0; i < 2400; i += 1) {
        await vi.advanceTimersByTimeAsync(100);
        await settle(store);
        const markup = render(store);
        const read = readScreen(markup, store);
        const headlineEnded = read.outcome.mission !== null && ENDED_HEADLINES.has(read.outcome.mission);
        const runLineEnded = read.outcome.run !== null && read.outcome.run !== 'running' && read.outcome.run !== 'none';
        if (headlineEnded || runLineEnded) sawEnd = true;
        const active = Object.entries(read.stages).filter(([, status]) => status === 'active').map(([step]) => step);
        const alreadyInProgress = read.readiness === 'not_ready' && /already in progress/i.test(read.readinessSentence);
        // The display's own words for that moment keep Stop on: the runtime has not said it let go of the run.
        if (read.readinessSentence.includes('has not yet reported itself idle') && read.off['stop'] !== false) bad.push(`+${((Date.now() - t0) / 1000).toFixed(1)} s Stop off beside "${read.readinessSentence}"`);
        if ((headlineEnded || runLineEnded) && (active.length > 0 || alreadyInProgress)) {
          bad.push(`+${((Date.now() - t0) / 1000).toFixed(1)} s headline=${String(read.outcome.mission)} run=${String(read.outcome.run)} active=[${active.join(',')}] readiness=${read.readiness} "${read.readinessSentence.slice(0, 60)}"`);
        }
        const snap = store.getSnapshot();
        const mission = snap.mission;
        if (!activated && mission?.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === 'capability_unavailable') {
          await vi.advanceTimersByTimeAsync(5500);
          for (const key of ACTIVATIONS[name] ?? []) {
            await store.activateCandidate(key);
            await vi.advanceTimersByTimeAsync(300);
          }
          activated = true;
        }
        if (!retried && mission?.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === 'operator_decision') {
          await vi.advanceTimersByTimeAsync(3000);
          await pressRetry(store);
          retried = true;
        }
        const ended = mission?.state === 'COMPLETE' || mission?.state === 'FAILED' || mission?.state === 'STOPPED';
        if (ended && snap.view.active_run === null && restAt < 0) restAt = i;
        if (restAt >= 0 && i > restAt + 80) break;
      }
      expect(sawEnd).toBe(true);
      expect(restAt).toBeGreaterThanOrEqual(0);
      expect(bad).toEqual([]);
      store.dispose();
    }, 120_000);
  }
});

/** A run p walked to Return, as the fold holds it; `ended` adds its run.finished. The snapshot still names p as active. */
function foldOf(ended: boolean): ViewState {
  const view = foldEvents(initialState(), [
    started('p'),
    enter('p', 'SEARCH'),
    enter('p', 'PLACEMENT_PLAN', 'SEARCH'),
    enter('p', 'BASE_ADVANCE', 'PLACEMENT_PLAN'),
    enter('p', 'PLACED', 'BASE_ADVANCE'),
    enter('p', 'BASE_RETURN', 'PLACED'),
    ...(ended ? [finished('p', 'placed_new', null)] : []),
  ]);
  return { ...view, active_run: { run_id: 'p', command_id: 'cmd-p', command_type: 'start_cycle', status: 'running', state: 'BASE_RETURN', started_seq: 1 } } as ViewState;
}

const RUN_ACTIVE: Capability = { id: 'start_cycle', state: 'disabled', reason_code: 'run_active', reason: 'a run is already in progress', dependency: null, planned: false };

describe('the rule the rows and the controls read', () => {
  it('a run whose end the fold holds has no step and no phase in progress, though the snapshot still names it', () => {
    expect(presentationSteps(foldOf(false)).rows.find((row) => row.step === 'return')?.status).toBe('active');
    expect(presentationSteps(foldOf(true)).rows.some((row) => row.status === 'active')).toBe(false);
    expect(presentationSteps(foldOf(false), 'another-run').rows.some((row) => row.status === 'active')).toBe(false);
    expect(phaseProgress(foldOf(false)).current).not.toBeNull();
    expect(phaseProgress(foldOf(true)).current).toBeNull();
  });

  it("says the snapshot's run_active in the display's voice only once the record holds that run's end", () => {
    expect(runEndedNotIdle(foldOf(true), RUN_ACTIVE)).toBe(RUN_ENDED_NOT_IDLE);
    expect(runEndedNotIdle(foldOf(false), RUN_ACTIVE)).toBeNull();
    expect(runEndedNotIdle({ ...foldOf(true), active_run: null }, RUN_ACTIVE)).toBeNull();
    expect(runEndedNotIdle(foldOf(true), { ...RUN_ACTIVE, reason_code: 'robot_not_ready' } as Capability)).toBeNull();
    expect(runEndedNotIdle(foldOf(true), { ...RUN_ACTIVE, state: 'enabled', reason_code: null } as Capability)).toBeNull();
  });
});
