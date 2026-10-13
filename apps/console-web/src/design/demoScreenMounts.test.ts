// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DEMO SCREEN MOUNTS THE SCENE MAP. The scene map (track U2's SceneMap, folded from the drawn runs' events) stands in
// the bottom strip, where the pad-relative stand-in stood. The Cake panel (the resident's modules, the controller's card,
// the slot check and the participation) moved to Diagnostics, Cake (spec 7.1); its tests are in diagnosticsMoves.test.ts.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { buildSceneScanRun } from '../api/syntheticScenarios';
import { foldEvents, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
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

function renderScreen(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function scanView(): ViewState {
  return foldEvents(initialState(), buildSceneScanRun(1, 1, 1_000_000_000).steps.map((step) => step.event));
}

describe('the scene map on the demo screen', () => {
  it('draws the scan of the drawn run from its events, and the stand-in is gone', () => {
    const markup = renderScreen(storeShowing(scanView()));
    expect(markup).toContain('data-region="scene"');
    expect(markup).toContain('data-view="minus_15"');
    expect(markup).toContain('data-block="BASE_ADVANCE"');
    expect(markup).not.toContain('pad-relative scene');
  });
});
