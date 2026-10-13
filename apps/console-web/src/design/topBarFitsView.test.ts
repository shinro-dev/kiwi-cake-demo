// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE TOP BAR'S DETAIL NEVER PUSHES STOP OUT OF VIEW (refix round 9, verify-r8 should_fix 2).
//
// The top bar is sticky and holds the only Stop. With "What this rests on" open, its lists grew the bar without bound: at
// 844 x 390 the bar measured 435 px (569 px in the verifier's case) in a 390 px view, and Stop was drawn below the bottom
// of the view, out of reach while the bar stayed stuck at the top. The detail's lists now scroll inside a box whose
// height is a fraction of the view's, so the open bar stays shorter than the view and Stop stays in it. The measured
// layout is checked in a real browser at 844 x 390, 390 x 844 and 1440 x 900; this pin holds the rule.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { modeMark } from '../model/modeMark';
import { readiness } from '../model/readiness';
import type { ReadyItem } from '../model/readyChecklist';
import { TopBar } from './TopBar';

const ITEMS: ReadyItem[] = (['runtime', 'contract', 'commands', 'robot', 'host', 'arm', 'motion', 'uncertain', 'wrist'] as const).map((id) => ({
  id,
  label: id,
  state: 'unmet',
  sentence: `a sentence about ${id} long enough to wrap on a narrow screen, as the runtime's own words often are`,
  hint: `what to do about ${id}`,
})) as ReadyItem[];

describe("the top bar's readiness detail", () => {
  it('scrolls its lists inside a box bounded by the height of the view', () => {
    const markup = renderToStaticMarkup(
      createElement(TopBar, {
        mode: modeMark({ dataMode: 'live', sourceKind: 'live', replaying: false }),
        robot: { label: 'robot', connection: 'connected', tone: 'ok', notReadSince: null },
        motion: null,
        readiness: readiness({ mission: null, runActive: false, startOff: 'a reason', checklist: ITEMS }),
        start: { label: 'Start', off: 'a reason', busy: false },
        stop: { off: null, busy: false },
        onStart: () => undefined,
        onStop: () => undefined,
        onDiagnostics: () => undefined,
      }),
    );
    const detail = /<details[^>]*data-readiness-detail=""[^>]*>([\s\S]*?)<\/details>/.exec(markup)?.[1] ?? '';
    expect(detail).toContain('data-ready-item="runtime"');
    const box = /<div[^>]*data-readiness-scroll=""[^>]*>/.exec(detail)?.[0] ?? '(no scroll box)';
    expect(box).toMatch(/max-height:[^;"]*vh/);
    expect(box).toMatch(/overflow-y:auto/);
    // Every item is inside the box: nothing of the lists grows the bar past it.
    const inside = detail.slice(detail.indexOf('data-readiness-scroll'));
    expect(inside).toContain('data-ready-item="runtime"');
    expect(inside).toContain('data-ready-item="wrist"');
  });
});
