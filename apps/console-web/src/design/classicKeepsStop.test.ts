// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CLASSIC SCREEN'S OVERLAYS LEAVE ITS ONLY STOP PRESSABLE (refix round 9, verify-r8 should_fix 1 and the M1 class).
//
// The classic screen (`?screen=classic`) keeps Stop in its header. Its Diagnostics drawer got no top and covered the whole
// view, header included (`design/Screen.tsx:1469` at d47aa9c), and its Start dialog had the demo screen's M1 defect: open
// over a run the record showed, its scrim over Stop, its "start the mission" on although Start was off. Now the header
// reports its height, the drawer and every dialog under the screen start below it, the header is drawn above them, the
// Start dialog is not drawn while the record shows a run, and its start is off whenever Start is off.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { runIsActive } from '../model/steps';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import type { ScreenOpening } from './DemoScreen';
import { Screen } from './Screen';
import { pressStart } from './startPress';

function asLive(store: ConsoleStore, patch: Partial<StoreSnapshot> = {}): void {
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view: { ...held.view, data_mode: 'live' } };
}

function render(store: ConsoleStore, opening: ScreenOpening): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen, { opening })));
}

function button(markup: string, pattern: RegExp): string {
  const tags = markup.match(/<button[^>]*>[^<]*<\/button>/g) ?? [];
  return tags.find((item) => pattern.test(item)) ?? '(not drawn)';
}

function zIndex(tag: string): number {
  return Number(/z-index:(\d+)/.exec(tag)?.[1] ?? 'NaN');
}

describe('the classic screen keeps its only Stop pressable', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  async function idle(): Promise<ConsoleStore> {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'one-block' }), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1234);
    return store;
  }

  it('the Diagnostics drawer starts at the header height, and the header is drawn above it', async () => {
    const store = await idle();
    const markup = render(store, { drawer: 'evidence', barHeight: 96 });
    const overlay = /<div[^>]*data-drawer-overlay=""[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
    expect(overlay).toMatch(/top:96px/);
    const header = /<header[^>]*data-region="classic-header"[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
    expect(zIndex(header)).toBeGreaterThan(zIndex(overlay));
  });

  it('the Start dialog starts below the header', async () => {
    const store = await idle();
    asLive(store);
    const markup = render(store, { attest: { allChecked: false }, barHeight: 96 });
    expect(markup).toContain('role="dialog"');
    expect(/<div[^>]*class="dialog-scrim"[^>]*>/.exec(markup)?.[0] ?? '').toMatch(/top:96px/);
  });

  it('the Start dialog is not drawn while the record shows a run, and Stop is on', async () => {
    const store = await idle();
    await pressStart(store);
    await vi.advanceTimersByTimeAsync(1500);
    expect(runIsActive(store.getSnapshot().view)).toBe(true);
    asLive(store);
    const markup = render(store, { attest: { allChecked: true } });
    expect(markup).not.toContain('role="dialog"');
    expect(button(markup, /data-control="stop"/)).not.toMatch(/ disabled=""/);
  });

  it('its "start the mission" is off, with Start\'s reason, whenever the header\'s Start is off', async () => {
    const store = await idle();
    asLive(store, { startingMission: true });
    const markup = render(store, { attest: { allChecked: true } });
    expect(markup).toContain('role="dialog"');
    expect(button(markup, />start the mission</)).toMatch(/ disabled=""/);
  });

  it('its "start the mission" is on with every box ticked and Start on', async () => {
    const store = await idle();
    asLive(store);
    const markup = render(store, { attest: { allChecked: true } });
    expect(button(markup, /data-control="start"/)).not.toMatch(/ disabled=""/);
    expect(button(markup, />start the mission</)).not.toMatch(/ disabled=""/);
  });

  it('the header wraps at a narrow view instead of laying Stop past the right edge, where the page clips it', async () => {
    const store = await idle();
    const markup = render(store, {});
    const header = /<header[^>]*data-region="classic-header"[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
    expect(header).toMatch(/flex-wrap:wrap/);
    const controls = /<div[^>]*data-region="classic-controls"[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
    expect(controls).not.toMatch(/flex:none/);
    expect(controls).toMatch(/max-width:100%/);
    // Start, Stop and Diagnostics wrap as a row of their own too.
    const row = /<div style="[^"]*flex-wrap:wrap[^"]*"><button[^>]*data-control="start"/.exec(markup);
    expect(row).not.toBeNull();
  });

  it('the header stays at the top of the view when the page scrolls (below the screen\'s 700 px minimum height)', async () => {
    const store = await idle();
    const markup = render(store, {});
    const header = /<header[^>]*data-region="classic-header"[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
    expect(header).toMatch(/position:sticky/);
    expect(header).toMatch(/top:0/);
    // The page root clips without being a scroll container of its own: under `overflow: hidden` a sticky header sticks to
    // the root, which never scrolls, and scrolls away with the page.
    const root = /^<div[^>]*>/.exec(markup)?.[0] ?? '';
    expect(root).not.toMatch(/overflow:hidden/);
    expect(root).toMatch(/overflow:clip/);
  });
});
