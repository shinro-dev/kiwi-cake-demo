// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SIMULATION SERVES ONLY WHAT IT ADMITS. For the plain script and for every scenario of the catalogue, each command
// capability the snapshot serves as enabled is admitted when it is submitted, and each one it serves as blocked is
// refused with the reason code the list gave. The DEMO screen draws its controls from this list, so a control drawn on
// is a control the rehearsal's own runtime takes. The mission Start is read the same way: it is on only where the
// scenario's mission start would begin a mission.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Command } from '@kiwi/demo-contracts';
import { SyntheticSource } from './synthetic';
import { SCENARIO_CATALOGUE } from './syntheticScenarios';
import type { ScenarioName } from './syntheticScenarios';

const COMMANDS: Record<string, Command> = {
  start_cycle: { type: 'start_cycle', args: { plan_only: false } },
  observe: { type: 'observe', args: { holding: false } },
  return_arm: { type: 'return_arm', args: { plan_only: false } },
  scan_scene: { type: 'scan_scene', args: { plan_only: false } } as unknown as Command,
};

interface Served {
  id: string;
  state: string;
  reason_code: string | null;
}

async function served(scenario: ScenarioName | null): Promise<Served[]> {
  const source = new SyntheticSource({ scenario });
  const snapshot = await source.snapshot();
  return snapshot.capabilities
    .filter((capability) => capability.id in COMMANDS)
    .map((capability) => ({ id: capability.id, state: capability.state, reason_code: capability.reason_code ?? null }));
}

async function pressed(scenario: ScenarioName | null, id: string): Promise<{ status: string; reason_code: string | null }> {
  const source = new SyntheticSource({ scenario });
  const outcome = await source.submit(`probe-${id}`, COMMANDS[id] as Command);
  if (outcome.kind !== 'result') throw new Error(`no result for ${id}`);
  return { status: outcome.result.status, reason_code: outcome.result.reason_code ?? null };
}

describe('the simulation serves as enabled exactly the commands it admits', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  const cases: Array<ScenarioName | null> = [null, ...SCENARIO_CATALOGUE.map((spec) => spec.name)];
  for (const scenario of cases) {
    it(`${scenario ?? 'the plain script'}: every command the list serves on is admitted, every one it serves off is refused with its code`, async () => {
      for (const capability of await served(scenario)) {
        const result = await pressed(scenario, capability.id);
        if (capability.state === 'enabled') {
          expect(result, `${capability.id} is served on`).toEqual({ status: 'running', reason_code: null });
        } else {
          expect(result, `${capability.id} is served off with ${String(capability.reason_code)}`).toEqual({ status: 'rejected', reason_code: capability.reason_code });
        }
      }
    });
  }

  it('Look and Scan scene are served on only where the scenario plays them', async () => {
    for (const spec of SCENARIO_CATALOGUE) {
      const list = await served(spec.name);
      const state = (id: string): string | undefined => list.find((item) => item.id === id)?.state;
      if (spec.robot === undefined) {
        expect(state('observe'), `${spec.name}: observe`).toBe(spec.look === undefined ? 'blocked' : 'enabled');
        expect(state('scan_scene'), `${spec.name}: scan_scene`).toBe(spec.scan === undefined ? 'blocked' : 'enabled');
      }
      if (spec.look === undefined) expect(list.find((item) => item.id === 'observe')?.reason_code, `${spec.name}: observe`).not.toBeNull();
      if (spec.scan === undefined) expect(list.find((item) => item.id === 'scan_scene')?.reason_code, `${spec.name}: scan_scene`).not.toBeNull();
    }
  });

  it('a scenario whose first press is not Start serves Start off with its own refusal, and its mission start refuses it the same way', async () => {
    const refusing = SCENARIO_CATALOGUE.filter((spec) => spec.start_refusal !== undefined);
    expect(refusing.map((spec) => spec.name).sort()).toEqual(['observe-landmarks-only', 'undo-to-inert']);
    for (const spec of refusing) {
      const start = (await served(spec.name)).find((item) => item.id === 'start_cycle');
      expect(start, spec.name).toEqual({ id: 'start_cycle', state: 'blocked', reason_code: spec.start_refusal?.reason_code });
      const source = new SyntheticSource({ scenario: spec.name });
      expect(await source.missionStart(), spec.name).toEqual({ kind: 'refused', reason_code: spec.start_refusal?.reason_code, detail: spec.start_refusal?.detail });
    }
  });
});
