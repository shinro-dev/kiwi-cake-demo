// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE TIMELINE REGION (spec rules 6.3 and 6.4, top 3): the store's append-only list, oldest first, each entry keyed and
// timed in UTC, each run a group under its heading; a list that follows its end until the operator scrolls away, and
// then a pill that counts the entries that arrived since. The follow state is `model/follow.ts`'s.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { FOLLOWING, follow } from '../model/follow';
import type { TimelineEntry } from '../model/missionTimeline';
import { TimelineRegion } from './TimelineRegion';

const ENTRIES: TimelineEntry[] = [
  { key: 'mission:m1:started', kind: 'mission', group: null, t: '2026-09-27T14:16:38.847Z', seq: null, text: 'Mission m1 started 14:16:38Z', verdict: null, more: [] },
  { key: 'run:r2', kind: 'run', group: 'r2', t: '2026-09-27T14:21:46.005Z', seq: -1, text: 'Run 2 of 2, r2, 14:21:46Z to 14:24:40Z (2 min 54 s)', verdict: null, more: [] },
  { key: 'r2:3633', kind: 'event', group: 'r2', t: '2026-09-27T14:21:50.097Z', seq: 3633, text: 'SEARCH 14:21:50Z', verdict: null, more: [] },
  { key: 'r2:3906', kind: 'event', group: 'r2', t: '2026-09-27T14:24:40.112Z', seq: 3906, text: 'Run 2 of 2 stopped by the controller, base_not_zero_before_segment', verdict: { id: 'stopped_by_controller', word: 'Stopped by the controller', tone: 'bad' }, more: [] },
  { key: 'press:press-1', kind: 'press', group: null, t: '2026-09-27T14:30:02.000Z', seq: null, text: 'You pressed Stop, 14:30:02Z (this display): Stop refused: no run is in progress.', verdict: null, more: [] },
];

function render(state = FOLLOWING, entries = ENTRIES): string {
  return renderToStaticMarkup(createElement(TimelineRegion, { entries, follow: state, onJump: () => undefined, onScroll: () => undefined, listRef: null, notReadSince: null }));
}

describe('the timeline region', () => {
  it('draws every entry in order, keyed, with its UTC time, as a polite log', () => {
    const markup = render();
    expect([...markup.matchAll(/data-key="([^"]+)"/g)].map((match) => match[1])).toEqual(ENTRIES.map((entry) => entry.key));
    expect([...markup.matchAll(/data-timeline-entry="([a-z]+)"/g)].map((match) => match[1])).toEqual(['mission', 'run', 'event', 'event', 'press']);
    for (const match of markup.matchAll(/data-t="([^"]+)"/g)) expect(match[1]).toMatch(/Z$/);
    expect(markup).toContain('role="log"');
    expect(markup).toContain('aria-live="polite"');
    expect(markup).toContain('Oldest first. Times in UTC.');
    expect(markup).toContain('data-verdict="stopped_by_controller"');
  });

  it('says it follows the end and draws no pill while following', () => {
    const markup = render(follow(FOLLOWING, { kind: 'appended', count: 2 }));
    expect(markup).toContain('data-follow="following"');
    expect(markup).not.toContain('data-control="timeline-jump"');
  });

  it('draws the pill with the count of entries that arrived after the operator scrolled away', () => {
    let state = follow(FOLLOWING, { kind: 'scrolled', atEnd: false });
    state = follow(state, { kind: 'appended', count: 3 });
    const markup = render(state);
    expect(markup).toContain('data-follow="paused"');
    expect(markup).toMatch(/data-control="timeline-jump"[^>]*data-unseen="3"/);
    expect(markup).toContain('3 new entries below');
  });

  it('draws no pill while paused with nothing new', () => {
    const markup = render(follow(FOLLOWING, { kind: 'scrolled', atEnd: false }));
    expect(markup).toContain('data-follow="paused"');
    expect(markup).not.toContain('data-control="timeline-jump"');
  });
});
