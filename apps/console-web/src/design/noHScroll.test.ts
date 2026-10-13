// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE PAGE NEVER SCROLLS SIDEWAYS (redesign r4 round 7 item 7; verify-r6 browser walk S8; spec 2.2).
//
// Every design button refused to wrap (`.dsbutton { white-space: nowrap }`, and the controls row's action button again),
// so a candidate's long label pushed the page to 518 px at a 390 px viewport and the tampered-twin button ran past its
// card. The layout itself is measured in a real browser (the round's walk); this pin holds the rule that makes it so.

import { describe, expect, it } from 'vitest';
import designCss from './design.css?raw';

function rule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) return '';
  return css.slice(at, css.indexOf('}', at));
}

describe('no sideways page scroll', () => {
  it('a design button may wrap its label and never fixes its height, so a long label grows it downwards, not the page sideways', () => {
    const button = rule(designCss, '.dsbutton');
    expect(button).not.toBe('');
    expect(button).not.toMatch(/white-space:\s*nowrap/);
    expect(button).toMatch(/max-width:\s*100%/);
    const small = rule(designCss, '.dsbutton--sm');
    expect(small).not.toMatch(/(^|[^-])height:\s*32px/);
    expect(small).toMatch(/min-height:\s*32px/);
    expect(rule(designCss, '.dc .controls-row .action__button')).not.toMatch(/white-space:\s*nowrap/);
    // The console's own small buttons on this screen, and the row the Observe holding choice sits in.
    expect(rule(designCss, '.dc .button')).toMatch(/white-space:\s*normal/);
    expect(rule(designCss, '.dc .action__ask-buttons')).toMatch(/flex-wrap:\s*wrap/);
  });
});
