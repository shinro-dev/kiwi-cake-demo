// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AT A RUN'S END THE CARD'S HEADLINE DOES NOT SAY "MISSION RUNNING" BESIDE THE RUN'S END (redesign r4 verify-r1 must_fix 6).
//
// The card read the mission record at start, after its own presses and on the 5 s poll; the stream's run.finished
// refreshed only the finished-run list. So for up to one poll the headline said "Mission running" beside "Run 1 of 1
// failed" (or placed, or needs capability) while the source's mission had already moved on. The stream's run.finished
// re-reads the mission record, and until a read asked after the run's end has answered, the headline says, in this
// display's voice, that the run ended and the mission record is being read.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import { SyntheticSource } from '../api/synthetic';
import { scenarioUrl } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import { scenarioFrom } from '../panels/sourceChoice';
import type { MissionRecord } from '../model/missionRecord';
import { outcomeCard } from '../model/outcomeCard';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { element, settle, textOf } from './demoScenarioPlay';
import { pressStart } from './startPress';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

const ENDED = ['failed', 'placed', 'needs_capability', 'stopped_by_controller', 'stopped_on_request', 'refused'];

describe("the mission card's headline at a run's end", () => {
  afterEach(() => vi.useRealTimers());

  for (const name of ['near-block-grasp-miss', 'scene-scan', 'one-block'] as ScenarioName[]) {
    it(`${name}: is never "running" while its run line says the run ended`, async () => {
      vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') });
      const source = new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(name)) });
      const store = new ConsoleStore(source, { storage: null });
      await store.start();
      await settle(store);
      if (name === 'scene-scan') {
        await store.submit({ type: 'scan_scene', args: { plan_only: false } } as unknown as Command);
        for (let slice = 0; slice < 200; slice += 1) {
          await vi.advanceTimersByTimeAsync(250);
          await settle(store);
          if (slice > 20 && store.getSnapshot().view.active_run === null) break;
        }
        await vi.advanceTimersByTimeAsync(6000);
        await settle(store);
      }
      await pressStart(store);
      const t0 = Date.now();
      const contradictions: string[] = [];
      let sawEnd = false;
      for (let slice = 0; slice < 1200; slice += 1) {
        await vi.advanceTimersByTimeAsync(250);
        await settle(store);
        const snap = store.getSnapshot();
        if (snap.mission?.state === 'WAITING_FOR_CAPABILITY' && snap.mission.waiting_reason === 'capability_unavailable' && snap.mission.activations.length === 0) {
          await store.activateCandidate('mobile-demo-swapped');
        }
        const markup = render(store);
        const card = element(markup, 'data-outcome-card=') ?? '';
        const headline = /data-verdict="([a-z_]+)"/.exec(card)?.[1] ?? null;
        const runLine = element(markup, 'data-run-line=') ?? '';
        const runVerdict = /data-run-verdict="([a-z_]+)"/.exec(runLine)?.[1] ?? null;
        if (runVerdict !== null && ENDED.includes(runVerdict)) sawEnd = true;
        if (headline === 'running' && runVerdict !== null && ENDED.includes(runVerdict)) {
          contradictions.push(`+${String(Date.now() - t0)} ms: Mission running beside "${textOf(runLine).slice(0, 80)}"`);
        }
        const state = snap.mission?.state ?? '';
        if (['COMPLETE', 'FAILED', 'STOPPED'].includes(state) && snap.view.active_run === null && slice > 40) break;
        if (snap.mission?.waiting_reason === 'operator_decision' && slice > 40) break;
      }
      expect(sawEnd).toBe(true);
      expect(contradictions).toEqual([]);
      store.dispose();
    }, 120_000);
  }
});

describe('the card model while the mission record says RUNNING and its run has ended', () => {
  const mission = {
    mission_record_version: '1', mission_id: 'mission-0001', state: 'RUNNING', data_mode: 'synthetic', performs_motion: false, execution_label: null,
    started_wall_utc: '2026-09-27T14:16:00.000Z', selected_target: 'cube', waiting_reason: null, detail: null, stop_requested: false, stop_requested_wall_utc: null,
    targets: [{ target_id: 'cube', runs: ['synthetic-run-0001'], attempts: [], measured_mode: null }], activations: [], hold: null, next_actions: [],
  } as unknown as MissionRecord;
  const finished = {
    seq: 9, kind: 'run.finished', run_id: 'synthetic-run-0001', t_wall_utc: '2026-09-27T14:16:05.000Z', reason_code: 'grasp_missed',
    payload: { outcome: 'failed', reason_code: 'grasp_missed', exit_status: 1 },
  } as unknown as ContractEvent;
  const input = {
    mission, missionError: null, history: [], runEvents: { 'synthetic-run-0001': [finished] }, activeRunId: null, runOnScreen: 'synthetic-run-0001',
    acknowledgements: [], capability: null, drawnRunEvents: {},
  };

  it('says the run ended and the mission record is being read, not "running"', () => {
    const card = outcomeCard(input);
    expect(card.verdict?.id).not.toBe('running');
    expect(card.verdict?.word).toBe('Run ended; reading the mission record');
    expect(card.run?.verdict?.id).toBe('failed');
  });

  it('says "running" while another run is in progress, and follows the record once it moves on', () => {
    expect(outcomeCard({ ...input, activeRunId: 'synthetic-run-0002' }).verdict?.id).toBe('running');
    expect(outcomeCard({ ...input, runEvents: {} }).verdict?.id).toBe('running');
    expect(outcomeCard({ ...input, mission: { ...mission, state: 'FAILED' } as MissionRecord }).verdict?.id).toBe('failed');
  });
});
