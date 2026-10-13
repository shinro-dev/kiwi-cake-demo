// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE LIVE STATUS IN THE MIDDLE, THE TIMELINE BELOW IT (docs/autonomous-demo/console-redesign-2026-09-27.md, section 2;
// operator layout ruling 2026-09-28): while a run or a mission runs, the stages, the scene and the wrist camera are the
// prime area in the middle of the screen, and the timeline is below them. When nothing runs the regions keep their places,
// so nothing jumps when a run starts or ends. The mission card and Cake stay at the top with the controls.
//
// The demo grid places its regions in document order (no `order`, no `grid-row` on a region), so the order of the regions
// in the markup is the order on the screen at every width; the stylesheet is read to keep it so.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { foldEvents, initialState } from '../model/reducer';
import { enter, placedRun, started } from '../model/stageFixtures';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import designCss from './design.css?raw';

function storeShowing(view: ViewState): ConsoleStore {
  const store = new ConsoleStore(new SyntheticSource());
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, view };
  return store;
}

function order(view: ViewState): string[] {
  const markup = renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(view) }, createElement(DemoScreen)));
  const grid = markup.slice(markup.indexOf('class="demo-grid"'));
  return [...grid.matchAll(/class="demo-grid__([a-z]+)"/g)].map((match) => match[1] ?? '');
}

function running(): ViewState {
  const runId = 'synthetic-running';
  return foldEvents(initialState(), [started(runId), enter(runId, 'SEARCH'), enter(runId, 'TARGET_SELECTED', 'SEARCH'), enter(runId, 'PREGRASP', 'TARGET_SELECTED')]);
}

describe('the demo screen places the live status in the middle and the timeline below it', () => {
  it('while a run runs: mission and Cake at the top, then stages, scene and wrist, then the timeline', () => {
    expect(order(running())).toEqual(['mission', 'cake', 'strip', 'timeline']);
  });

  it('when nothing runs, and after a run ended, the same regions keep the same places', () => {
    expect(order(initialState())).toEqual(['mission', 'cake', 'strip', 'timeline']);
    expect(order(placedRun('r'))).toEqual(['mission', 'cake', 'strip', 'timeline']);
  });

  it('the strip carries the stages, the scene and the wrist camera, in that order', () => {
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(running()) }, createElement(DemoScreen)));
    const strip = markup.slice(markup.indexOf('class="demo-grid__strip"'), markup.indexOf('class="demo-grid__timeline"'));
    const regions = [...strip.matchAll(/data-region="([a-z]+)"/g)].map((match) => match[1]);
    expect(regions.filter((name) => name === 'stages' || name === 'scene' || name === 'wrist')).toEqual(['stages', 'scene', 'wrist']);
  });

  it('no region of the demo grid is moved out of document order by the stylesheet, at any width', () => {
    const rules = [...designCss.matchAll(/([^{}]*\.demo-grid__[a-z]+[^{}]*)\{([^}]*)\}/g)];
    expect(rules.length).toBeGreaterThan(0);
    for (const rule of rules) expect(rule[2] ?? '').not.toMatch(/(^|[;\s])(order|grid-row|grid-area)\s*:/);
  });
});
