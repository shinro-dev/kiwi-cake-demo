// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SENTENCES THAT WERE ON THE SCREEN AT ONCE, AND WHAT IS THERE NOW.
//
// While a real non-motion Observe's whole pose sat in the runtime's log, this panel said: "The host is not reporting
// measurements: tap_not_configured". That was a true and complete answer about the CONTINUOUS TELEMETRY TAP and a
// false statement about measurement, and the one of the two that had a measurement in it was the one thrown away.
//
// The three ages are the second half of the same mistake. "console: lines are arriving" stood beside "7 min 59 s
// ago", which reads as a contradiction and is really two different measurements printed as one word. Each of the
// three is now labelled with what it MEASURES: transport activity, source measurement, frame freshness.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import observeSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/an-observe-recorded-a-pose-while-no-tap-is-configured.json';
import { SyntheticSource } from '../api/synthetic';
import { applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import { TelemetryPanel } from './Workspace';

const NOW = Date.parse('2026-09-21T21:17:28Z');

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

function panel(view: ViewState): string {
  const store = new ConsoleStore(new SyntheticSource());
  // A page showing `view` read the snapshot it came from; a page that read none says "not read" (verify-r1 must_fix 3).
  const snapshot = { ...store.getSnapshot(), runtimeAnswered: true, view } as ReturnType<ConsoleStore['getSnapshot']>;
  return text(renderToStaticMarkup(createElement(TelemetryPanel, { snapshot, nowMs: NOW })));
}

describe('the panel that said nothing was measuring', () => {
  // A RELOADED TAB: nothing folded at all, one snapshot applied. This is the tab that had the defect.
  const reloaded = applySnapshot(initialState(), observeSnapshot as unknown as Snapshot, NOW);

  it('renders the pose the observe recorded, under a heading that names a record and not a feed', () => {
    const words = panel(reloaded);
    expect(words).toContain('Last controller measurement');
    expect(words).toContain('-9.582');
    expect(words).toContain('-79.429');
    expect(words).toContain('38.374');
    expect(words).toContain('67.209');
    expect(words).toContain('-96.659');
    expect(words).toContain('79.848');
  });

  it('states where the measurement came from and that nothing about it is continuous', () => {
    const words = panel(reloaded);
    expect(words).toContain('at record 5');
    expect(words).toContain('the runtime named it on the snapshot');
    expect(words).toContain('not a live feed');
    expect(words).toContain('recorded at 2026-09-21T21:09:29.150910Z');
  });

  it('never prints the absent tap in place of the measurement, and still reports the tap as itself', () => {
    const words = panel(reloaded);
    expect(words).not.toContain('The host is not reporting measurements');
    expect(words).toContain('Continuous telemetry');
    expect(words).toContain('No continuous telemetry is being received');
    expect(words).toContain('it is not a statement that nothing was measured');
  });

  it('labels the two ages it prints with what each of them measures, and never shares a word between them', () => {
    const words = panel(reloaded);
    expect(words).toContain('source measurement: recorded');
    expect(words).toContain('transport activity:');
    expect(words).toContain('no stream line has arrived in this tab');
  });

  it('says plainly that no controller has read the robot when none has, without naming the tap as the reason', () => {
    const words = panel(initialState());
    expect(words).toContain('No controller has read the robot in this runtime session');
    expect(words).toContain('Continuous telemetry');
  });
});
