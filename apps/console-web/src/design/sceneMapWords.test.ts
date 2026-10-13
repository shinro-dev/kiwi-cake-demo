// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAPABILITY WORDS OF THE SCENE MAP FOLLOW THE LIVE SLOT (spec rule 5.7.5, audit C8). On 2026-09-27 the map said the
// base advance "needs the Cake capability" while the robot reported mobile_approach active at generation 2. The words
// are now read from GET /api/robot's `capability`: active, not active, or not read, and never a fixed text.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { buildSceneScanRun } from '../api/syntheticScenarios';
import { baseCapabilityWords, foldSceneMap } from '../model/sceneMap';
import type { RobotCapability, RobotStatus } from '../model/robotStatus';
import { liveSlot } from '../model/liveSlot';
import type { LiveSlot } from '../model/liveSlot';
import { SceneMap } from './SceneMap';

function capability(active: boolean): RobotCapability {
  return { configured: true, name: 'mobile_approach', active, reason_code: active ? null : 'slot_not_active', capsule: '2f34a84c1971', generation: 2, child_pid: null, stale: false, observed: null };
}

/** The live slot as a robot reading on this page says it. */
function slot(active: boolean): LiveSlot | null {
  const robot = { configured: true, capability: capability(active) } as unknown as RobotStatus;
  return liveSlot({ robot, robotReadAtMs: Date.parse('2026-09-27T14:30:00.000Z'), mission: null, day: '2026-09-27' });
}

function text(html: string): string {
  return html.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
}

const SCAN: ContractEvent[] = buildSceneScanRun(1, 0, 1_000_000_000).steps.map((step) => step.event);

describe('the capability words beside a block that needs the base advance', () => {
  it('names the active capability and its generation when the live slot carries it', () => {
    expect(baseCapabilityWords(slot(true))).toBe("the base advance uses mobile_approach, active at generation 2 (as the robot's reading says)");
  });

  it('says the capability is not active when the live slot does not carry it', () => {
    expect(baseCapabilityWords(slot(false))).toBe("needs mobile_approach, which is not active (as the robot's reading says)");
  });

  it('says the robot has not been read when there is no robot answer', () => {
    expect(baseCapabilityWords(null)).toBe('needs the base capability; the robot has not been read');
  });

  it('draws the live words beside the BASE_ADVANCE block and never the fixed Cake sentence', () => {
    const active = text(renderToStaticMarkup(createElement(SceneMap, { map: foldSceneMap(SCAN), slot: slot(true) })));
    expect(active).toContain('placing it needs the base advance (108 mm planned): the base advance uses mobile_approach, active at generation 2');
    const unread = text(renderToStaticMarkup(createElement(SceneMap, { map: foldSceneMap(SCAN), slot: null })));
    expect(unread).toContain('needs the base capability; the robot has not been read');
    for (const words of [active, unread]) expect(words).not.toContain('needs the Cake capability');
  });
});
