// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DIAGNOSTICS DRAWER LEAVES THE ONLY STOP PRESSABLE (redesign r4 verify-r7 must_fix M1).
//
// The drawer's overlay was `position: fixed; inset: 0; z-index: 50`, above the sticky top bar (z-index 20) that holds the
// only Stop: with Evidence or History open while the robot moved, a press where Stop is drawn landed on the drawer, at
// 1440 px and at 390 px. The overlay now starts below the top bar, at the bar's measured height, so the bar and its Stop
// stay uncovered and pressable while the drawer is open. The measured layout is checked in a real browser (the round's
// walk: `document.elementFromPoint` at Stop's centre returns Stop with the drawer open); these pins hold the rules.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import { Drawer } from './Drawers';
import { observeHeight, TopBar } from './TopBar';
import type { TopBarProps } from './TopBar';

const BAR = {
  mode: { id: 'live', word: 'Live', title: '' },
  robot: { label: 'robot', connection: 'answering', tone: 'ok', notReadSince: null },
  motion: null,
  readiness: { verdict: { tone: 'wait', words: 'Running' }, sentence: null, startPrimary: false, checked: [], unchecked: [] },
  start: { label: 'Start', off: 'a run is active', busy: false },
  stop: { off: null, busy: false },
  onStart: () => undefined,
  onStop: () => undefined,
  onDiagnostics: () => undefined,
} as unknown as TopBarProps;

function drawerRoot(top: number | undefined): string {
  const store = new ConsoleStore(new SyntheticSource(), { storage: null });
  const markup = renderToStaticMarkup(
    createElement(
      StoreContext,
      { value: store },
      createElement(Drawer, { id: 'evidence', snapshot: store.getSnapshot(), store, mode: 'synthetic', top, onSelect: () => undefined, onClose: () => undefined }),
    ),
  );
  return /^<div[^>]*>/.exec(markup)?.[0] ?? '';
}

function zIndex(tag: string): number {
  return Number(/z-index:(\d+)/.exec(tag)?.[1] ?? 'NaN');
}

describe('the Diagnostics drawer leaves the only Stop pressable', () => {
  it('the overlay starts at the top bar height it is given, not at the top of the view', () => {
    const root = drawerRoot(214);
    expect(root).toMatch(/position:fixed/);
    expect(root).toMatch(/top:214px/);
    expect(root).not.toMatch(/inset:0/);
    expect(root).toMatch(/bottom:0/);
  });

  it('the top bar is drawn above the overlay, so a rounding pixel of overlap still lands on the bar', () => {
    const header = /<header[^>]*data-region="top-bar"[^>]*>/.exec(renderToStaticMarkup(createElement(TopBar, BAR)))?.[0] ?? '';
    expect(zIndex(header)).toBeGreaterThan(zIndex(drawerRoot(214)));
  });

  it("the top bar reports its own height as it changes (it wraps at 390 px and when its detail opens)", () => {
    let height = 58.4;
    let fire: (() => void) | null = null;
    let disconnected = false;
    class FakeObserver {
      constructor(callback: () => void) {
        fire = callback;
      }
      observe(): void {}
      disconnect(): void {
        disconnected = true;
      }
    }
    const seen: number[] = [];
    const element = { getBoundingClientRect: () => ({ height }) } as unknown as HTMLElement;
    const stop = observeHeight(element, (px) => seen.push(px), FakeObserver as unknown as typeof ResizeObserver);
    expect(seen).toEqual([59]);
    height = 251.2;
    (fire as unknown as () => void)();
    expect(seen).toEqual([59, 252]);
    stop();
    expect(disconnected).toBe(true);
  });

  it('with no observer in this environment it still reports the height once', () => {
    const seen: number[] = [];
    const element = { getBoundingClientRect: () => ({ height: 64 }) } as unknown as HTMLElement;
    observeHeight(element, (px) => seen.push(px), undefined)();
    expect(seen).toEqual([64]);
  });

  it('the demo screen hands its top bar height to the drawer it opens (the wiring, not only the drawer)', () => {
    const store = new ConsoleStore(new SyntheticSource(), { storage: null });
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen, { opening: { drawer: 'evidence', barHeight: 214 } })));
    const overlay = /<div[^>]*data-drawer-overlay=""[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
    expect(overlay).toMatch(/top:214px/);
  });
});
