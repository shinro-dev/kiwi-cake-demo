// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// SCAN SCENE ON THE DEMO SCREEN. The control sends the scan_scene command (track P0b) through the Actions panel, so it
// makes every refusal the other motion controls make: the uncertain-command block (scan_scene is a MOTION control), the
// server's capability list, the lease and the robot answer. It is the primary look; Observe stays beside it.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Capability } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import { blockedByUncertainty, MOTION_CONTROLS } from '../model/uncertain';
import { placedRun } from '../model/stageFixtures';
import type { ViewState } from '../model/types';
import { scanSceneCommand } from '../panels/Actions';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';

function storeShowing(view: ViewState, patch: Partial<StoreSnapshot> = {}): ConsoleStore {
  const store = new ConsoleStore(new SyntheticSource());
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view };
  return store;
}

function scanButton(markup: string): string {
  const match = /<button[^>]*data-control="scan_scene"[^>]*>/.exec(markup);
  return match === null ? '' : match[0];
}

function withCapabilities(view: ViewState, capabilities: Capability[]): ViewState {
  return { ...view, data_mode: 'synthetic', contract_compatible: true, commands_enabled: true, capabilities };
}

const ENABLED = (id: string): Capability => ({ id, state: 'enabled', reason: null, reason_code: null, dependency: null, planned: false }) as unknown as Capability;

describe('scan_scene is a motion control', () => {
  it('is in MOTION_CONTROLS, so an uncertain command blocks it', () => {
    expect(MOTION_CONTROLS).toContain('scan_scene');
    const unresolved = { block_reason_code: 'owner_unresolved' } as unknown as Parameters<typeof blockedByUncertainty>[0];
    expect(blockedByUncertainty(unresolved, 'scan_scene')).toBe('owner_unresolved');
  });

  it('builds the contract command with no holding flag and no plan-only', () => {
    expect(scanSceneCommand()).toEqual({ type: 'scan_scene', args: { plan_only: false } });
  });
});

describe('the Scan scene control on the demo screen', () => {
  it('is an enabled command control when the runtime offers the capability', () => {
    const view = withCapabilities(placedRun('p'), [ENABLED('scan_scene'), ENABLED('observe'), ENABLED('return_arm')]);
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(view) }, createElement(DemoScreen)));
    const button = scanButton(markup);
    expect(button).not.toBe('');
    expect(button).toContain('aria-disabled="false"');
    // Scan scene is never the primary control of the demo screen (spec rule 4.3).
    expect(button).not.toContain('button--primary');
    expect(button).not.toContain('data-primary');
    expect(markup).not.toContain('sends no scan_scene command yet');
  });

  it('is off with the runtime reason when the runtime does not offer it', () => {
    const view = withCapabilities(placedRun('p'), [ENABLED('observe')]);
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(view) }, createElement(DemoScreen)));
    expect(scanButton(markup)).toContain('aria-disabled="true"');
    expect(markup).toContain('this runtime does not report this capability');
  });
});
