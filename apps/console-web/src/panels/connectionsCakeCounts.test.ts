// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// CONNECTIONS COUNTS THE RESIDENT'S SLOTS FROM THE RESIDENT (verdict 2026-09-27 item 11). On the saved 8771 records the
// engineering reference's Cake panel said "0 slot(s)", "slots the resident reported 0" and "No component in this
// execution path is an active Cake module", while the same tab listed the resident's three slots with mobile_approach
// ACTIVE at generation 2 and the main screen's live slot said active. The 0 was the runtime snapshot's Cake view
// (`cake.slots: []`) and its components list, printed in the resident's name. The count and the active slots now come
// from the resident's served slots (GET /api/robot), the reading the main screen's live slot comes from; what the
// snapshot says is said as the snapshot's.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { Drawer } from '../design/Drawers';
import { robotStatus } from '../model/robotStatusFixture';
import { placedRun } from '../model/stageFixtures';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { CakePanel } from './SystemPanels';

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/\s+/g, ' ');
}

/** The resident as 8771 served it at 18:33Z: three slots, the capability ACTIVE at generation 2. */
const RESIDENT = robotStatus({
  slots: {
    stale: false,
    reason_code: null,
    observed: null,
    items: [
      { name: 'supervisor.lekiwi', uuid: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', capsule: '1111111111111111', generation: 1, instance: '600', state: 'ACTIVE' },
      { name: 'mobile_approach', uuid: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', capsule: '2f34a84c1971', generation: 2, instance: '601', state: 'ACTIVE' },
      { name: 'mobile_approach.holder', uuid: 'cccccccccccccccccccccccccccccccc', capsule: '9600566de6c9', generation: 2, instance: '602', state: 'STAGED' },
    ],
  },
});

/** The runtime snapshot's own Cake view on 8771: no slot, and no component of the execution path marked a Cake module. */
const VIEW: ViewState = { ...placedRun('p'), data_mode: 'live' };

describe("Connections' Cake counts are the resident's (verdict 2026-09-27 item 11)", () => {
  it('counts the slots the resident lists and names its active ones, never the snapshot view\'s 0', () => {
    expect(VIEW.cake.slots.length).toBe(0);
    const words = text(renderToStaticMarkup(createElement(CakePanel, { view: VIEW, robot: RESIDENT, robotError: null })));
    expect(words).not.toContain('0 slot(s)');
    expect(words).not.toMatch(/slots the resident reported 0/);
    expect(words).toContain('slots the resident reported 3');
    expect(words).toContain('3 slot(s) the resident lists');
    expect(words).toContain('Active in the resident: supervisor.lekiwi (generation 1), mobile_approach (generation 2).');
    // The snapshot's statements are the snapshot's, never an unattributed claim about the execution path.
    expect(words).not.toMatch(/(^|[.] )No component in this execution path is an active Cake module/);
    expect(words).toContain("The runtime's snapshot lists no component of this execution path as an active Cake module.");
  });

  it('says the resident was not read, never a count, when the robot read failed or has not come back', () => {
    const failed = text(renderToStaticMarkup(createElement(CakePanel, { view: VIEW, robot: null, robotError: 'robot_unreachable' })));
    expect(failed).not.toMatch(/slots the resident reported \d/);
    expect(failed).toContain('not read: robot_unreachable');
    const unread = text(renderToStaticMarkup(createElement(CakePanel, { view: VIEW, robot: null, robotError: null })));
    expect(unread).toContain('not read yet');
    expect(unread).not.toContain('0 slot(s)');
  });

  it('reads the resident in the Connections tab, as the main screen does', () => {
    const store = new ConsoleStore(new SyntheticSource(), { storage: null });
    const held = store.getSnapshot();
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, view: VIEW, robot: RESIDENT, robotReadAtMs: Date.now(), robotError: null };
    const words = text(
      renderToStaticMarkup(
        createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'connections', snapshot: store.getSnapshot(), store, mode: 'live', onSelect: () => undefined, onClose: () => undefined })),
      ),
    );
    expect(words).toContain('slots the resident reported 3');
    expect(words).not.toContain('0 slot(s)');
  });
});
