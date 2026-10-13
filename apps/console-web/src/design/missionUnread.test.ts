// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A MISSION READ THAT FAILED IS SAID AS NOT READ (owner's status 2026-09-27, LOW 4; verifier r3 defect 4). With the
// mission read failing at load, the timeline said "Nothing recorded on this display yet." and Observe "No record read
// here says whether the gripper is holding the cube", beside Stages drawn from the run's own records. Neither sentence
// was true: the mission, which is what the timeline is built from and what settles holding, was not read. Both now say
// so, with the read's own code, and a runtime that answered it has no mission keeps the old words.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource } from '../api/types';
import { initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { Actions } from '../panels/Actions';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

/** The synthetic source, whose mission read answers `reason` and nothing else. */
function missionFails(reason: string): ConsoleSource {
  const source = new SyntheticSource({ scenario: 'one-block' });
  return new Proxy(source, {
    get(target, property, receiver) {
      if (property === 'mission') return () => Promise.resolve({ ok: false, reason_code: reason, detail: null });
      const value = Reflect.get(target, property, receiver) as unknown;
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  }) as ConsoleSource;
}

function timelineRegion(markup: string): string {
  const at = markup.indexOf('data-region="timeline"');
  expect(at).toBeGreaterThan(-1);
  return text(markup.slice(at, markup.indexOf('</section>', at)));
}

describe('the timeline with the mission not read', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:21:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('says the mission has not been read, with its code, never that nothing was recorded', async () => {
    const store = new ConsoleStore(missionFails('mission_read_failed'), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(store.getSnapshot().missionError).toBe('mission_read_failed');
    const words = timelineRegion(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen))));
    expect(words).toContain('The mission has not been read: mission_read_failed.');
    expect(words).not.toContain('Nothing recorded on this display yet');
    store.dispose();
  });

  it('keeps "Nothing recorded on this display yet" when the runtime answered that it has no mission', async () => {
    const store = new ConsoleStore(missionFails('no_mission_recorded'), { storage: null });
    await store.start();
    await vi.advanceTimersByTimeAsync(1000);
    const words = timelineRegion(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen))));
    expect(words).not.toContain('The mission has not been read');
    store.dispose();
  });
});

describe('Observe with the mission not read', () => {
  const view: ViewState = {
    ...initialState(),
    data_mode: 'live',
    capabilities: [{ id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }],
  };

  function observe(patch: Record<string, unknown>): string {
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), view, ...patch } as ReturnType<ConsoleStore['getSnapshot']>;
    return text(renderToStaticMarkup(createElement(Actions, { snapshot, store, only: ['observe'] })));
  }

  it('says the mission record that settles holding was not read, never that no record read here says', () => {
    const words = observe({ mission: null, missionError: 'mission_read_failed' });
    expect(words).toContain('The mission record, which settles whether the gripper is holding the cube, has not been read (mission_read_failed)');
    expect(words).not.toContain('No record read here says');
    // It is still asked, once, and the control stays off until it is answered.
    expect(words).toContain('Off until you state whether the gripper is holding the cube.');
  });

  it('keeps its words when the runtime answered that it has no mission', () => {
    const words = observe({ mission: null, missionError: 'no_mission_recorded' });
    expect(words).toContain('No record read here says whether the gripper is holding the cube');
    expect(words).not.toContain('has not been read');
  });
});
