// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// EVERY SCENARIO OF THE CATALOGUE, THROUGH THE REAL STORE AND ON THE RENDERED SCREEN. `syntheticScenarios.test.ts`
// pins what each scenario records, the press outcomes and the mission states; this pins only that the page opened at
// its address takes it: the store starts over the scenario's source, the operator's first press is made, and the screen
// renders a non-empty page while the scenario plays, marked as the simulation at the end, and never crashes.
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { Screen } from '../design/Screen';
import { scenarioFrom } from '../panels/sourceChoice';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { SyntheticSource } from './synthetic';
import { SCENARIO_CATALOGUE, scenarioUrl } from './syntheticScenarios';

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
}

describe('every scenario, opened at its address, on the rendered screen', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  for (const spec of SCENARIO_CATALOGUE) {
    it(`${spec.name} (${scenarioUrl(spec.name)})`, async () => {
      const source = new SyntheticSource({ scenario: scenarioFrom(scenarioUrl(spec.name)) });
      const store = new ConsoleStore(source);
      await store.start();
      const idle = render(store);
      expect(idle.length).toBeGreaterThan(0);
      expect(store.getSnapshot().view.machine).toBeNull();

      if (spec.trigger === 'start') await source.missionStart();
      if (spec.trigger === 'look') {
        await source.submit('scenario-look', { contract_version: '1', command_id: 'scenario-look', type: 'observe', args: { holding: false }, client: { session_id: 'test', contract_version: '1' } } as Command);
      }
      if (spec.trigger === 'check') {
        const check = await source.capabilityCheck();
        if (!check.ok) throw new Error('no check');
        const candidate = check.state.candidates[0];
        if (candidate === undefined) throw new Error('no candidate');
        await source.applyCapabilityCheck(candidate.key, candidate.payload_digest ?? '', check.state.current.session_uuid ?? '');
      }
      for (let slice = 0; slice < 480; slice += 1) {
        await vi.advanceTimersByTimeAsync(250);
        if (slice % 40 === 0) expect(render(store).length).toBeGreaterThan(0);
      }
      // The classic screen marks the simulation in its own words; the SYNTHETIC mark of the new screen is track U1's.
      const end = render(store);
      expect(end).toMatch(/Rehearsal|simulation/);
      store.dispose();
    });
  }
});
