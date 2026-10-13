// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SHOW SCREEN (operator ruling 2026-10-11): the four-click demo's screen. Rendered to static markup with a store whose
// snapshot is patched, the way design/vlaOnScreen.test.ts does; effects do not run, so what is asserted is the markup.
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
import { ShowScreen } from './ShowScreen';

function storeWith(vlaDoor: boolean, patch: Partial<StoreSnapshot>): ConsoleStore {
  const base = new SyntheticSource();
  const doors = vlaDoor
    ? { vlaStatus: async (): Promise<VlaStatus> => ({ state: 'standby' }), vlaCommand: async () => ({ ok: true, answer: null }) }
    : { vlaStatus: undefined, vlaCommand: undefined };
  const source = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, doors);
  const store = new ConsoleStore(source, { storage: null, sessionStorage: null });
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), ...patch };
  return store;
}

const render = (store: ConsoleStore): string => renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(ShowScreen)));

const ID = 'mission-7f6e958dd257';
const held = parseMission({
  mission_record_version: '1', mission_id: ID, state: 'STOPPED', data_mode: 'live', performs_motion: true, execution_label: null, started_wall_utc: '2026-10-11T15:12:40Z',
  selected_target: 'cube', waiting_reason: null, detail: null, activations: [], stop_requested: true,
  targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'STOPPED', runs: ['run-2'],
    attempts: [{ attempt: 1, run_id: 'run-2', outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE', measurements: [], observation_ids: [],
      stop_state: { base_ahead: false, holding: true, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T15:15:47Z' }] }],
});

describe('the show screen', () => {
  it('carries the controls, the five stages, both cameras and Cake, and neither the scene map nor the log', () => {
    const markup = render(storeWith(true, {}));
    for (const control of ['start', 'stop', 'enable-vla', 'diagnostics']) expect(markup).toContain(`data-control="${control}"`);
    for (const region of ['top-bar', 'mission', 'stages', 'front', 'wrist', 'cake']) expect(markup).toContain(`data-region="${region}"`);
    for (const absent of ['data-region="scene"', 'data-region="timeline"', 'data-control="scan_scene"', 'data-control="return_arm"', 'vla-panel']) expect(markup).not.toContain(absent);
    for (const label of ['Front camera', 'Wrist camera', 'Find', 'Pick', 'Carry', 'Place', 'Return', 'Enable VLA', 'Mission 1: scripted pick and place', 'Cake on the robot']) expect(markup).toContain(label);
    expect(markup.indexOf('data-region="mission"')).toBeLessThan(markup.indexOf('data-region="wrist"'));
    expect(markup.indexOf('data-region="wrist"')).toBeLessThan(markup.indexOf('data-region="front"'));
    expect(markup).not.toContain('text-transform');
    expect(markup.match(/data-control="stop"/g)?.length).toBe(1);
  });

  it('shows mission 1 finished by the carry of this mission, with Enable VLA offered', () => {
    const vla: VlaStatus = { state: 'standby', model_sha256: 'a'.repeat(64), carry: { result: 'done', mission_id: ID }, events: [] };
    const markup = render(storeWith(true, { mission: held, vla, demoArmed: ID, directorStep: { kind: 'finished' } }));
    expect(markup).toContain('Mission 1 complete');
    expect(markup).toMatch(/data-stage="return"[^>]*data-stage-status="verified"/);
    expect(markup).toMatch(/data-control="enable-vla"[^>]*aria-disabled="false"/);
    expect(markup).not.toContain('data-control="candidate"');
  });

  it('once the VLA module is enabled, Start is the VLA run and the button says the module is enabled', () => {
    const vla: VlaStatus = { state: 'ready', model_sha256: 'a'.repeat(64), warm_inference_ms: 10700 };
    const markup = render(storeWith(true, { vla }));
    expect(markup).toContain('Mission 2: VLA pick and place');
    expect(markup).toContain('Put the block back');
    expect(markup).toContain('VLA enabled');
    expect(markup).toMatch(/data-control="start"[^>]*title="Start the mission/);
  });

  it('while this tab\'s Start is still bringing the arm to the mission start, Stop is on, though the runtime has no run to stop', () => {
    // the runtime's own list says Stop is off (no run yet); the runner has not been read moving yet
    const noRun = storeWith(true, {}).getSnapshot().view;
    const view = { ...noRun, capabilities: [{ id: 'stop', state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop', dependency: 'owner', planned: false }] } as unknown as StoreSnapshot['view'];
    const idle = render(storeWith(true, { view, demoStarting: false, vla: { state: 'standby' }, vlaMaybeDriving: false }));
    expect(idle).toMatch(/data-control="stop"[^>]*aria-disabled="true"/);
    const starting = render(storeWith(true, { view, demoStarting: true, vla: { state: 'standby' }, vlaMaybeDriving: false }));
    expect(starting).toMatch(/data-control="stop"[^>]*aria-disabled="false"/);
  });

  it('while the VLA policy drives the arm, Stop is on and the stages follow the episode', () => {
    const vla: VlaStatus = { state: 'running', events: [{ t: 1, phase: 'at_start' }, { t: 90, phase: 'latched' }, { t: 97, phase: 'object_secured', value: true, score: 0.978 }, { t: 110, phase: 'thinking', chunk: 9 }] };
    const markup = render(storeWith(true, { vla, vlaMaybeDriving: true }));
    expect(markup).toMatch(/data-control="stop"[^>]*aria-disabled="false"/);
    expect(markup).toContain('data-readiness="running"');
    expect(markup).not.toContain('the VLA module is not ready');
    expect(markup).toMatch(/data-stage="pick"[^>]*data-stage-status="verified"/);
    expect(markup).toMatch(/data-stage="carry"[^>]*data-stage-status="active"/);
    expect(markup).toContain('The VLA policy is driving the arm');
  });
});
