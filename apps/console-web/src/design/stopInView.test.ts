// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// STOP STAYS IN VIEW (redesign r4 round 7 item 6; verify-r6 browser walk S1).
//
// The only Stop sits in the top bar, which was not sticky: right after a press that starts motion (an activation far down
// the page, Scan scene, Try again, Return arm) the page stood where the pressed button was and Stop was hundreds of
// pixels above the view (measured -391 at 390 px and -431 at 1440 px). The layout itself is measured in a real browser
// (the round's walk); this pin holds the rule that makes it so.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { TopBar } from './TopBar';
import type { TopBarProps } from './TopBar';

const PROPS = {
  mode: { id: 'live', word: 'Live', title: '' },
  robot: { label: 'robot', connection: 'answering', tone: 'ok', notReadSince: null },
  motion: null,
  readiness: { verdict: { tone: 'ok', words: 'Ready' }, sentence: null, startPrimary: false, checked: [], unchecked: [] },
  start: { label: 'Start', off: null, busy: false },
  stop: { off: null, busy: false },
  onStart: () => undefined,
  onStop: () => undefined,
  onDiagnostics: () => undefined,
} as unknown as TopBarProps;

describe('the only Stop stays in view', () => {
  it('the top bar that holds the only Stop is sticky at the top of the view, over an opaque background', () => {
    const markup = renderToStaticMarkup(createElement(TopBar, PROPS));
    const header = /<header[^>]*data-region="top-bar"[^>]*>/.exec(markup)?.[0] ?? '';
    expect(header).toMatch(/position:sticky/);
    expect(header).toMatch(/top:0/);
    expect(header).toMatch(/background:/);
    expect(header).toMatch(/z-index:/);
  });

});
