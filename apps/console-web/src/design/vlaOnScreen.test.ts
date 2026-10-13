// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE VLA BEAT ON THE DEMO SCREEN: the panel stands above the demo grid (outside it), only on a console that has the VLA door, and
// while the robot-side runner drives the arm the mission Start is off for that reason.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource } from '../api/types';
import { parseMission } from '../model/missionRecord';
import type { VlaStatus } from '../model/vla';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen } from './DemoScreen';

function storeWith(vlaDoor: boolean, patch: Partial<StoreSnapshot>): ConsoleStore {
  const base = new SyntheticSource();
  const doors = vlaDoor
    ? { vlaStatus: async (): Promise<VlaStatus> => ({ state: 'standby' }), vlaCommand: async () => ({ ok: true, answer: null }) }
    : { vlaStatus: undefined, vlaCommand: undefined };
  const source = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, doors);
  const store = new ConsoleStore(source);
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), ...patch };
  return store;
}

function render(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function startButton(markup: string): string {
  return /<button[^>]*data-control="start"[^>]*>/.exec(markup)?.[0] ?? '';
}

describe('the VLA panel on the demo screen', () => {
  it('while the VLA policy drives the arm: the panel says so with Stop, above the grid, and Start is off for it', () => {
    const markup = render(storeWith(true, { vla: { state: 'running', model_sha256: 'a'.repeat(64), events: [{ t: 3, phase: 'thinking', chunk: 2 }] } }));
    expect(markup).toContain('The VLA policy is driving the arm');
    expect(markup).toContain('data-control="vla-stop"');
    // above the demo grid, under the alerts, so it is on screen without scrolling; never inside <main>
    expect(markup.indexOf('console__vla')).toBeGreaterThan(-1);
    expect(markup.indexOf('console__vla')).toBeLessThan(markup.indexOf('<main class="demo-grid"'));
    const start = startButton(markup);
    expect(start).toContain('disabled');
  });

  it('a stopped mission that holds the block: the panel offers the carry; once carried, mission 1 finished and the swap', () => {
    const mission = parseMission({
      mission_record_version: '1', mission_id: 'mission-31e2813f6d21', state: 'STOPPED', data_mode: 'live', performs_motion: true, execution_label: null,
      started_wall_utc: '2026-10-11T12:35:39Z', selected_target: 'cube', waiting_reason: null, detail: null, activations: [],
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'STOPPED', runs: ['console-20261011T123601Z-m48fbbe3'],
        attempts: [{ attempt: 1, run_id: 'console-20261011T123601Z-m48fbbe3', outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE',
          measurements: [], observation_ids: [], stop_state: { base_ahead: false, holding: true, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T12:38:27.657Z' }] }],
    });
    const holding = render(storeWith(true, { mission, vla: { state: 'standby', model_sha256: 'a'.repeat(64) } }));
    expect(holding).toContain('The mission holds the block');
    expect(holding).toContain('data-control="vla-carry"');
    expect(holding).not.toContain('data-control="vla-swap"');
    const finished = render(storeWith(true, { mission, vla: { state: 'standby', model_sha256: 'a'.repeat(64), carry: { result: 'done', mission_id: 'mission-31e2813f6d21' } } }));
    expect(finished).toContain('Mission 1 finished');
    expect(finished).toContain('data-control="vla-swap"');
    expect(finished).not.toContain('data-control="vla-carry"');
  });

  it('a mission of an earlier runtime session is History\'s: no carry is offered for it after a restart', () => {
    const mission = parseMission({
      mission_record_version: '1', mission_id: 'mission-31e2813f6d21', state: 'STOPPED', data_mode: 'live', performs_motion: true, execution_label: null,
      started_wall_utc: '2026-10-11T12:35:39Z', selected_target: 'cube', waiting_reason: null, detail: null, activations: [],
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'STOPPED', runs: ['console-20261011T123601Z-m48fbbe3'],
        attempts: [{ attempt: 1, run_id: 'console-20261011T123601Z-m48fbbe3', outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE',
          measurements: [], observation_ids: [], stop_state: { base_ahead: false, holding: true, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T12:38:27.657Z' }] }],
    });
    const base = storeWith(true, {}).getSnapshot();
    const markup = render(storeWith(true, { mission, view: { ...base.view, runtime_session: 'rt-20261011T130000Z-abc12345' }, vla: { state: 'standby' } }));
    expect(markup).toContain('Before the mission: bring the arm to its start pose');
    expect(markup).not.toContain('data-control="vla-carry"');
  });

  it('a console without the VLA door draws no panel', () => {
    const markup = render(storeWith(false, { vla: { state: 'running' } }));
    expect(markup).not.toContain('vla-panel');
    expect(markup).not.toContain('console__vla');
  });
});
