// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE LOGS TAB PROMISES NOTHING (redesign report 2026-09-27, residual R5 of verifier r3): the capability process's chip
// read "(to be provided)", a statement about work nobody has scheduled. What is true is that the runtime does not serve
// that output; the chip says so, on its face as in its title.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { initialState } from '../model/reducer';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Drawer } from './Drawers';

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/\s+/g, ' ');
}

describe('the Logs tab', () => {
  it('says the capability process output is not served by the runtime, never that it is to be provided', () => {
    const store = new ConsoleStore(new SyntheticSource(), { storage: null });
    const held = store.getSnapshot();
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, view: { ...initialState(), data_mode: 'live' } };
    const markup = renderToStaticMarkup(
      createElement(
        StoreContext,
        { value: store },
        createElement(Drawer, { id: 'logs', snapshot: store.getSnapshot(), store, mode: 'live', onSelect: () => undefined, onClose: () => undefined }),
      ),
    );
    const words = text(markup);
    expect(words).not.toContain('to be provided');
    expect(words).toContain('Capability process (not served by the runtime)');
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*title="the capability process output is not served by the runtime"/);
  });
});
