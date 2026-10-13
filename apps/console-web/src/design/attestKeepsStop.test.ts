// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE START ATTESTATION DIALOG NEVER STANDS OVER THE ONLY STOP WHILE A RUN IS ON THE RECORD (redesign r4 verify-r8 M1).
//
// On a runtime that is not the simulation, Start opens the "Before the mission starts" dialog. It closed only on Escape, a
// press on its scrim, cancel or "start the mission". A run another display started (or any run the record shows) left it
// open, its scrim (fixed, z-index 50) over the sticky top bar (z-index 20) that holds the only Stop: a press where Stop is
// drawn closed the dialog and sent nothing, and "start the mission" stayed on although the runtime would refuse it.
// Now the dialog is not drawn while the record shows a run, and "start the mission" is off, with Start's own reason on the
// screen, whenever the top bar's Start is off. The measured layout is checked in a real browser (the round's walk:
// `document.elementFromPoint` at Stop's centre returns Stop); these pins hold the rules over the server-rendered screen.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { runIsActive } from '../model/steps';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';
import type { ScreenOpening } from './DemoScreen';
import { pressStart } from './startPress';

/** The store's snapshot as a runtime that is not the simulation would serve it: the path that asks for the checks. */
function asLive(store: ConsoleStore, patch: Partial<StoreSnapshot> = {}): void {
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view: { ...held.view, data_mode: 'live' } };
}

function render(store: ConsoleStore, opening: ScreenOpening): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen, { opening })));
}

function button(markup: string, pattern: RegExp): string {
  const tags = markup.match(/<button[^>]*>[^<]*<\/button>/g) ?? [];
  return tags.find((item) => pattern.test(item)) ?? '(not drawn)';
}

describe('the Start attestation dialog and the only Stop', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  async function idle(): Promise<ConsoleStore> {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'one-block' }), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1234);
    return store;
  }

  it('is drawn after a Start press on an idle runtime that is not the simulation, and its start is on once every box is ticked', async () => {
    const store = await idle();
    asLive(store);
    const markup = render(store, { attest: { allChecked: true } });
    expect(markup).toContain('role="dialog"');
    expect(button(markup, /data-control="start"/)).not.toMatch(/ disabled=""/);
    expect(button(markup, />start the mission</)).not.toMatch(/ disabled=""/);
  });

  it('is not drawn once the record shows a run (another display pressed Start), and Stop is on and under no scrim', async () => {
    const store = await idle();
    await pressStart(store);
    await vi.advanceTimersByTimeAsync(1500);
    expect(runIsActive(store.getSnapshot().view)).toBe(true);
    asLive(store);
    const markup = render(store, { attest: { allChecked: true } });
    expect(markup).not.toContain('role="dialog"');
    expect(markup).not.toContain('dialog-scrim');
    expect(button(markup, /data-control="stop"/)).not.toMatch(/ disabled=""/);
  });

  it('its "start the mission" is off, with Start\'s own reason on the screen, whenever the top bar\'s Start is off', async () => {
    const store = await idle();
    asLive(store, { commandInFlight: true });
    const markup = render(store, { attest: { allChecked: true } });
    expect(markup).toContain('role="dialog"');
    expect(button(markup, /data-control="start"/)).toMatch(/ disabled=""/);
    expect(button(markup, />start the mission</)).toMatch(/ disabled=""/);
    expect(markup).toMatch(/data-control-reason="attest-start"[^>]*>Start is off\. /);
  });

  it('its "start the mission" is off while a box is unticked, as before', async () => {
    const store = await idle();
    asLive(store);
    const markup = render(store, { attest: { allChecked: false } });
    expect(button(markup, />start the mission</)).toMatch(/ disabled=""/);
  });
});
