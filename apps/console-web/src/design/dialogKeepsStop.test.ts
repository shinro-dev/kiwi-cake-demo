// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// NO DIALOG'S SCRIM COVERS THE ONLY STOP (refix round 9, the class of verify-r8 M1).
//
// Every dialog (the Start dialog, the cycle's checks, "Confirm the connected robot") drew its scrim fixed at inset 0 with
// z-index 50, over the sticky top bar (z-index 20) that holds the only Stop: whatever the dialog asked, a press where Stop
// is drawn landed on the scrim and closed the dialog, and sent nothing. The scrim now starts below the bar, at the height
// the screen measured for it and hands down to every dialog under it, so the bar and its Stop stay uncovered. A dialog on
// a screen with no bar that holds Stop starts at the top of the view, as before. The measured layout is checked in a real
// browser (`document.elementFromPoint` at Stop's centre returns Stop with a dialog open); these pins hold the rules.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Dialog } from '../ui/Dialog';
import { StopBarHeight } from '../ui/stopBar';
import { DemoScreen } from './DemoScreen';

function scrim(markup: string): string {
  return /<div[^>]*class="dialog-scrim"[^>]*>/.exec(markup)?.[0] ?? '(not drawn)';
}

function dialog(): ReturnType<typeof createElement> {
  return createElement(Dialog, { title: 'a question', onClose: () => undefined, actions: null, children: 'words' });
}

describe('a dialog leaves the only Stop pressable', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('its scrim starts at the height of the bar that holds Stop, handed down by the screen', () => {
    const tag = scrim(renderToStaticMarkup(createElement(StopBarHeight, { value: 214 }, dialog())));
    expect(tag).toMatch(/top:214px/);
  });

  it('with no bar above it, the scrim starts at the top of the view', () => {
    expect(scrim(renderToStaticMarkup(dialog()))).toMatch(/top:0(px)?[;"]/);
  });

  it('the demo screen hands its top bar height to the Start dialog', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'one-block' }), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1234);
    const held = store.getSnapshot();
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, view: { ...held.view, data_mode: 'live' } };
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen, { opening: { attest: { allChecked: false }, barHeight: 214 } })));
    expect(markup).toContain('role="dialog"');
    expect(scrim(markup)).toMatch(/top:214px/);
  });
});
