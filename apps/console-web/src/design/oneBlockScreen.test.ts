// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ONE-BLOCK REHEARSAL, ON THE RENDERED SCREEN, THROUGH THE STORE. `api/oneBlock.test.ts` pins the source's
// record; this pins what an operator sees when the page is opened at `?source=simulation&scenario=one-block` and
// Start is pressed: Start is offered, the card says it is scanning with the sector, the mission header and the
// Cake panel say it is held with the capability request offered, and after the activation and the carrying run the
// header counts one target done in the rehearsal's words. It drives the real store over the real source with a fake
// clock, so the poll the store makes is the poll the page makes.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Screen } from './Screen';
import { pressStart } from './startPress';

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

function controlTag(markup: string, name: string): string {
  const match = new RegExp(`<[a-z]+[^>]*data-control="${name}"[^>]*>`).exec(markup);
  if (match === null) throw new Error(`no ${name} control in the markup`);
  return match[0];
}

/** Let the fake clock run until the store's mission read says the wanted state, polling as the page polls. */
async function until(store: ConsoleStore, wanted: (snapshot: ReturnType<ConsoleStore['getSnapshot']>) => boolean): Promise<void> {
  for (let slice = 0; slice < 400; slice += 1) {
    if (wanted(store.getSnapshot())) return;
    await vi.advanceTimersByTimeAsync(250);
  }
  throw new Error('the screen did not reach the wanted state');
}

describe('the one-block rehearsal, as the operator sees it', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('offers Start, scans, holds with the request offered, resumes through the activation and completes', async () => {
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'one-block' }));
    await store.start();
    const render = (): string => renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));

    // Before Start: a mission may be started, and nothing is playing.
    expect(store.getSnapshot().missionError).toBe('no_mission_recorded');
    expect(controlTag(render(), 'start')).not.toContain('disabled=""');
    expect(text(render())).toContain('none on this runtime');

    // Start, then the sweep: the card says it is scanning, with the sector, and the rail is still on Find.
    const receipt = await pressStart(store);
    expect(receipt.tone).toBe('taken');
    await until(store, (snapshot) => snapshot.view.machine?.state === 'SEARCH_SWEEP');
    await vi.advanceTimersByTimeAsync(100);
    const scanning = text(render());
    expect(scanning).toContain('Scanning for the block');
    expect(scanning).toContain('Sector LEFT');
    expect(scanning).toContain('the block, being rehearsed');

    // The measuring run ends and the mission is held: the header, the action card, the Cake panel and its request.
    await until(store, (snapshot) => snapshot.mission?.state === 'WAITING_FOR_CAPABILITY' && snapshot.missionCandidates !== null);
    const held = text(render());
    expect(held).toContain('Held, waiting for a capability');
    expect(held).toContain('Activate this candidate');
    expect(held).toContain('mobile-demo-swapped');
    expect(held).toContain('Rehearsal: 0 of 1 done');
    expect(held).toContain('the block, held');
    expect(held).not.toContain('cubes placed');

    // The abort-swap: aborted, slot unchanged, still held. Then the swapped Plan: observed, the same target resumes.
    await store.activateCandidate('mobile-demo-active-successor-abort-swap');
    const aborted = text(render());
    expect(aborted).toContain('Refused as expected, slot unchanged');
    expect(aborted).not.toContain('The transaction aborted');
    expect(aborted).toContain('Activate this candidate');
    await store.activateCandidate('mobile-demo-swapped');
    await vi.advanceTimersByTimeAsync(100);
    const resumed = text(render());
    expect(resumed).toContain('Activated');
    expect(resumed).not.toContain('Activate this candidate');

    // The carrying run to the end: the mission completes, and the count is the rehearsal's, in the singular.
    await until(store, (snapshot) => snapshot.mission?.state === 'COMPLETE');
    const done = text(render());
    expect(done).toContain('Rehearsal: 1 of 1 done');
    expect(done).toContain('complete');
    expect(done).not.toContain('placed on the pad');
    store.dispose();
  });
});
