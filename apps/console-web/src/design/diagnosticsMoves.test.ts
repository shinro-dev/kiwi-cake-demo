// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT MOVED TO DIAGNOSTICS, AND WHAT WENT (spec section 7; audit C1, C9, the API-only item, T5). The capability slot check
// (its Apply buttons and its records, each with its session and date), the resident's modules with the holder slot and
// the controller's card, the signature table's rows and the activations' details are the new Cake tab's. The holder card
// says no "by design". The demo screen draws none of it.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import { robotStatus } from '../model/robotStatusFixture';
import { placedRun } from '../model/stageFixtures';
import { buildStoppedHoldingRun } from '../api/syntheticScenarios';
import { foldEvents, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import type { ContractEvent } from '../contracts/envelope';
import type { ScreenMode } from '../panels/StatusStrip';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { fixtureCheckAnswer } from './cakeFixtures';
import { DemoScreen } from './DemoScreen';
import { Drawer } from './Drawers';
import type { DrawerId } from './Drawers';

function storeShowing(view: ViewState, patch: Partial<StoreSnapshot> = {}): ConsoleStore {
  const store = new ConsoleStore(new SyntheticSource(), { storage: null });
  const held = store.getSnapshot();
  // A page showing `view` read the snapshot it came from; a page that read none says "not read" (verify-r1 must_fix 3).
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, runtimeAnswered: true, ...patch, view };
  return store;
}

/**
 * A page showing `live` whose run `runId` is read back and folded through the store's own `hydrateRun`, from a source
 * that serves `events` for it: the fold is the store's, not a fixture's.
 */
async function storeHydrating(live: ViewState, runId: string, events: ContractEvent[], patch: Partial<StoreSnapshot> = {}): Promise<ConsoleStore> {
  const base = new SyntheticSource();
  const source = new Proxy(base, {
    get(target, key) {
      if (key === 'historyEvents') return async () => ({ ok: true, run_id: runId, events, origins: [] });
      const value: unknown = Reflect.get(target, key, target);
      return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  });
  const store = new ConsoleStore(source, { storage: null });
  (store as unknown as { liveView: ViewState }).liveView = live;
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view: live };
  await store.hydrateRun(runId);
  return store;
}

function drawer(store: ConsoleStore, id: DrawerId, mode: ScreenMode = 'synthetic'): string {
  return renderToStaticMarkup(
    createElement(StoreContext, { value: store }, createElement(Drawer, { id, snapshot: store.getSnapshot(), store, mode, onSelect: () => undefined, onClose: () => undefined })),
  );
}

function screen(store: ConsoleStore): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
}

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
}

const HOLDER_ROBOT = robotStatus({
  slots: {
    stale: false,
    reason_code: null,
    observed: null,
    items: [
      { name: 'mobile_approach', uuid: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', capsule: '2f34a84c1971', generation: 2, instance: '601', state: 'ACTIVE' },
      { name: 'mobile_approach.holder', uuid: 'cccccccccccccccccccccccccccccccc', capsule: '9600566de6c9', generation: 2, instance: '602', state: 'ACTIVE' },
    ],
  },
  health: {
    stale: false,
    reason_code: null,
    observed: null,
    items: [{ slot: 'cccccccccccccccccccccccccccccccc', state_name: 'UNKNOWN', child_pid: 0, restarts: 0, spawns: 0, last_status: 0 }],
  },
});

describe('the Cake tab of Diagnostics', () => {
  const check = parseCapabilityCheckState(fixtureCheckAnswer());

  it('carries the modules, the holder with no "by design", and the controller card', () => {
    const store = storeShowing(placedRun('p'), { robot: HOLDER_ROBOT, robotReadAtMs: Date.now() });
    const markup = drawer(store, 'cake');
    expect(markup).toContain('data-cake-module="mobile_approach.holder"');
    expect(markup).toContain('data-cake-module="controller"');
    expect(text(markup)).not.toContain('by design');
    expect(text(markup)).toContain('UNKNOWN');
  });

  it('offers every prepared Plan of the slot check as its own button, and dates each check with its session', () => {
    expect(check).not.toBeNull();
    const store = storeShowing(placedRun('p'), { robot: robotStatus(), robotReadAtMs: Date.now(), capabilityCheck: check, capabilityCheckReadAtMs: Date.now() });
    const markup = drawer(store, 'cake');
    const keys = (check?.roles ?? []).map((role) => role.key);
    // More than one Plan, so the loop below cannot pass on an empty list (afeab8f demoScreenMounts.test.ts:66).
    expect(keys.length).toBeGreaterThan(1);
    for (const key of keys) expect(markup).toContain(`data-cake-candidate="${key}"`);
    expect(markup).not.toMatch(/aria-pressed="true"|data-chosen="true"/);
    expect(markup).toMatch(/data-check-session="[^"]+"/);
    expect(markup).toMatch(/data-check-t="[^"]*Z"/);
  });

  it('says why no Plan can be applied while no check has been read', () => {
    const markup = drawer(storeShowing(placedRun('p'), { capabilityCheck: null }), 'cake');
    expect(markup).toContain('No Plan can be applied from here');
  });

  it('reads participation from the runtime-served cake ladder', () => {
    const view = placedRun('p');
    const served = { ...view, cake: { ...view.cake, ladder: { ...view.cake.ladder, mission_participation: { value: true, evidence: 'mobile_approach carried the base for target cube' } } } };
    const markup = drawer(storeShowing(served as ViewState), 'cake');
    expect(markup).toContain('data-cake-participation="true"');
    // The runtime's own evidence for the participation is said with it (afeab8f demoScreenMounts.test.ts:82).
    expect(markup).toContain('mobile_approach carried the base for target cube');
  });

  it('holds the signature table without its Run button, which stays on the main card only', () => {
    const state = {
      signature_check_version: '1', in_flight: false, off_reason: null, policy: 'require', checks_dropped: 0,
      checks: [{
        check_uuid: 'c1', runtime_session: 'rt-earlier', t_wall_utc: '2026-09-26T20:26:13.435Z', policy: 'require', trusted_keys: [], throwaway_public_key: null, capsules: 1, unsigned_twins: 0,
        rows: [{ capsule: '2f34a84c1971', kind: 'signed_capsule', key: 'deployment', expected: 'admitted', expected_reason: null, observed: 'admitted', reason_code: null, admitted: true, as_expected: true }],
        rows_as_expected: 1, rows_not_as_expected: 0, rows_skipped: 0, rows_error: 0, as_expected: true, complete: true, duration_ms: 40,
      }],
    };
    const markup = drawer(storeShowing({ ...placedRun('p'), data_mode: 'live' }, { signatureCheck: state }), 'cake');
    expect(markup).toContain('data-signature-row="admitted"');
    expect(markup).not.toContain('data-control="signature-check-run"');
  });
});

describe('the demo screen, after the moves', () => {
  it('draws no module card, no holder, no slot check and no "by design"', () => {
    const check = parseCapabilityCheckState(fixtureCheckAnswer());
    const markup = screen(storeShowing(placedRun('p'), { robot: HOLDER_ROBOT, robotReadAtMs: Date.now(), capabilityCheck: check, capabilityCheckReadAtMs: Date.now() }));
    expect(markup).not.toContain('data-cake-module');
    expect(markup).not.toContain('data-cake-candidate');
    expect(markup).not.toContain('data-cake-panel');
    expect(text(markup)).not.toContain('by design');
  });
});

describe('the orders and defaults of Diagnostics (spec 6.6, audit T5)', () => {
  const entry = (run: string, finished: string): Record<string, unknown> => ({
    run_id: run, command_id: null, command_type: 'start_cycle', started_wall_utc: finished, finished_wall_utc: finished, outcome: 'failed', exit_status: 5,
    evidence_complete: true, evidence_gaps: 0, artifact_digest: null, adopted: false, data_mode: 'live', bundle_digest: null,
  });
  // The runtime serves its finished runs oldest first, as 8771 does.
  const history = { ok: true as const, entries: [entry('run-a', '2026-09-27T10:00:00.000Z'), entry('run-b', '2026-09-27T11:00:00.000Z'), entry('run-c', '2026-09-27T12:00:00.000Z')] } as unknown as StoreSnapshot['history'];
  const mission = {
    mission_record_version: '1', mission_id: 'mission-1', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null, started_wall_utc: null,
    selected_target: 'cube', waiting_reason: null, detail: null, targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: ['run-a', 'run-b'] }],
    activations: [],
  } as unknown as StoreSnapshot['mission'];

  it('lists the finished runs newest first in History, and says so', () => {
    const markup = drawer(storeShowing(placedRun('p'), { history, mission: null }), 'history');
    expect(text(markup)).toContain('newest first');
    const order = ['run-a', 'run-b', 'run-c'].map((id) => markup.indexOf(`>${id}<`)).filter((at) => at >= 0);
    expect(order).toHaveLength(3);
    expect(order[2]).toBeLessThan(order[1] ?? 0);
    expect(order[1]).toBeLessThan(order[0] ?? 0);
  });

  it("opens the Logs run picker on the mission's last run, else on the newest finished run", () => {
    const withMission = drawer(storeShowing(placedRun('p'), { history, mission }), 'logs');
    expect(withMission).toMatch(/<option value="run-b" selected="">/);
    const without = drawer(storeShowing(placedRun('p'), { history, mission: null }), 'logs');
    expect(without).toMatch(/<option value="run-c" selected="">/);
    expect(without.indexOf('value="run-c"')).toBeLessThan(without.indexOf('value="run-a"'));
  });
});

describe("the runtime snapshot's statements about itself (the API-only item)", () => {
  // As 8771 served them on 2026-09-27: the robot host "never contacted", the link unknown, cake_update not built.
  const view = {
    ...placedRun('p'),
    data_mode: 'live' as const,
    link: { state: 'unknown', source_stamp: null, receive_age_ms: null },
    components: [
      { id: 'robot-host', layer: 'process', role: 'the hardware host process on the robot', health: 'unknown', active: null, version: null, evidence: 'the runtime never contacts the robot host and no telemetry source is attached' },
    ],
    capabilities: [
      { id: 'cake_update', state: 'disabled', reason_code: 'not_built', reason: 'this runtime reads a resident read-only; no command of this contract can submit an update to one', dependency: 'cake_resident', planned: true },
    ],
  } as unknown as ViewState;

  it('never reaches the demo screen', () => {
    const markup = screen(storeShowing(view, { robot: HOLDER_ROBOT, robotReadAtMs: Date.now() }));
    expect(markup).not.toContain('not_built');
    expect(text(markup)).not.toContain('never contacts the robot host');
    expect(text(markup)).not.toContain('Link state');
  });

  it('is in Connections, under a heading that names its source', () => {
    const markup = drawer(storeShowing(view, { robot: HOLDER_ROBOT, robotReadAtMs: Date.now() }), 'connections');
    const words = text(markup);
    const at = words.indexOf("What the runtime's snapshot says about itself");
    expect(at).toBeGreaterThanOrEqual(0);
    const section = words.slice(at);
    expect(section).toContain('never contacts the robot host');
    expect(section).toContain('not_built');
    expect(section).toContain('link: unknown');
  });
});

describe("Evidence reads the stop's cause as the mission card reads it (audit C4, C5)", () => {
  const events = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event);
  const view = { ...foldEvents(initialState(), events), data_mode: 'live' as const };

  it("states the stop's own joined reading and the predicates it counted, never that none was recorded", () => {
    const words = text(drawer(storeShowing(view), 'evidence'));
    expect(words).toContain('base_zero: 0 reads against 3 reads');
    expect(words).toContain('rotation 1.17 deg/s');
    expect(words).toMatch(/\d+ predicates recorded, 1 tied to the stop/);
    expect(words).not.toContain('No measured value was recorded');
  });

  it('reads the run from its own records on a page opened after the run, never the empty live fold (C4)', async () => {
    // A page opened after the run on a live runtime: the live fold holds the run's outcome and none of its records.
    // The run's records are read back and folded BY THE STORE (`hydrateRun`), as the screen does, never hand-made here.
    const live = { ...initialState(), data_mode: 'live' as const, outcome: view.outcome };
    const runId = view.outcome.run_id ?? '';
    const store = await storeHydrating(live, runId, events);
    expect(store.getSnapshot().hydrated?.run_id).toBe(runId);
    const words = text(drawer(store, 'evidence', 'live'));
    expect(words).toMatch(/\d+ predicates recorded, 1 tied to the stop/);
    expect(words).not.toMatch(/(^|\s)0 recorded/);
    expect(words).not.toContain('No predicate has been recorded in this run');
    expect(words).toContain('base_zero');
  });

  it("names a live runtime's frame as recorded, never as the rehearsal's, on a page opened after the run (verifier r2 defect 1)", async () => {
    // The store folds a read-back run from the initial state, whose data mode is not the runtime's; the mode comes from
    // the live snapshot or the run's history entry.
    const live = { ...initialState(), data_mode: 'live' as const, outcome: view.outcome };
    const runId = view.outcome.run_id ?? '';
    const store = await storeHydrating(live, runId, events);
    const words = text(drawer(store, 'evidence', 'live'));
    expect(words).toContain('Recorded frame, not live.');
    expect(words).not.toContain('Generated by the rehearsal');
  });

  it("never words a live runtime's run as the rehearsal's result when its history entry is not read (verifier r2 defect 1)", async () => {
    // The result section's mode, like the frame's, comes from the run's history entry or the live snapshot, never from
    // the read-back fold's initial-state mode.
    const live = { ...initialState(), data_mode: 'live' as const, outcome: view.outcome };
    const runId = view.outcome.run_id ?? '';
    const store = await storeHydrating(live, runId, events);
    expect(store.getSnapshot().history).toBeNull();
    const words = text(drawer(store, 'evidence', 'live'));
    expect(words).not.toContain('The rehearsal recorded');
    expect(words).not.toContain('nothing was physically moved by it');
    expect(words).not.toMatch(/(^|\s)Rehearsal(\s|$)/);
  });

  it("reads Connections' engineering reference from the run's own records on a page opened after the run (verifier r2 defect 4)", async () => {
    // The live fold holds none of the run's records; the reference's decisions and run counts read the run's read-back
    // fold, as Evidence does, and never say that nothing was recorded or that no state was entered twice.
    const live = { ...initialState(), data_mode: 'live' as const, outcome: view.outcome };
    const runId = view.outcome.run_id ?? '';
    const store = await storeHydrating(live, runId, events);
    const words = text(drawer(store, 'connections', 'live'));
    expect(words).not.toContain('No predicate has been recorded in this run');
    expect(words).not.toContain('No state has been entered twice');
    expect(words).toContain('ALIGN entered again');
  });

  it('counts the predicates in words that agree, and never frames unrelated values as the stop record (verifier r2 defect 7)', () => {
    const words = text(drawer(storeShowing(view), 'evidence'));
    // The verification badge counts the newest verdict of each predicate, beside the count of every predicate record.
    expect(words).toMatch(/\d+ predicates recorded, 1 tied to the stop/);
    expect(words).toMatch(/\d+ predicates, newest verdict of each/);
    expect(words).not.toMatch(/(^|\s)\d+ recorded(\s|$)/);
    // Run m6ed2dd9's stop carried no values (failure.measurements []): the listed ones are the run's false predicates,
    // and the note says so, never that they are what the stop's record carried.
    expect(words).not.toContain('everything the record carried under it');
    expect(words).toContain("every predicate of this run that measured false; the stop's record carried no values");
    // A stop whose record carried a value: the note names both.
    const valued = events.map((event) => {
      if (event.kind !== 'run.finished') return event;
      const payload = event.payload as unknown as Record<string, unknown>;
      const failure = payload['failure'] as Record<string, unknown>;
      return { ...event, payload: { ...payload, failure: { ...failure, measurements: [{ name: 'base_zero_reads', value: 0, unit: null }] } } } as unknown as ContractEvent;
    });
    const carried = text(drawer(storeShowing({ ...foldEvents(initialState(), valued), data_mode: 'live' }), 'evidence'));
    expect(carried).toContain("the values the stop's record carried and every predicate of this run that measured false");
  });

  it("names a rehearsal run's frame as generated from its history entry, whatever the fold says", async () => {
    const live = { ...initialState(), data_mode: 'live' as const, outcome: view.outcome };
    const runId = view.outcome.run_id ?? '';
    const entry = { run_id: runId, command_id: null, command_type: 'start_cycle', started_wall_utc: null, finished_wall_utc: null, outcome: 'stopped', exit_status: 0, evidence_complete: true, evidence_gaps: 0, artifact_digest: null, adopted: false, data_mode: 'synthetic', bundle_digest: null };
    const store = await storeHydrating(live, runId, events, { history: { ok: true, entries: [entry] } as unknown as StoreSnapshot['history'] });
    expect(text(drawer(store, 'evidence', 'live'))).toContain('Generated by the rehearsal, not a camera image.');
  });

  it('carries no reason-code next-step table of its own', () => {
    const words = text(drawer(storeShowing(view), 'evidence'));
    expect(words).not.toContain('no next step for this reason code');
    expect(words).not.toContain('what can be done next');
  });
});

describe('the mode words in Diagnostics (spec 5.2, audit C12)', () => {
  it('names a run and a mission in the same four words as the top bar, never LIVE RUNTIME or PHYSICAL', () => {
    const entry = { run_id: 'run-a', command_id: null, command_type: 'start_cycle', started_wall_utc: '2026-09-27T10:00:00.000Z', finished_wall_utc: '2026-09-27T10:01:00.000Z', outcome: 'failed', exit_status: 5, evidence_complete: true, evidence_gaps: 0, artifact_digest: null, adopted: false, data_mode: 'live', bundle_digest: null };
    const mission = {
      mission_record_version: '1', mission_id: 'mission-1', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null, started_wall_utc: null,
      selected_target: 'cube', waiting_reason: null, detail: null, targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: ['run-a'] }],
      activations: [],
    } as unknown as StoreSnapshot['mission'];
    const markup = drawer(storeShowing({ ...placedRun('p'), data_mode: 'live' }, { mission, history: { ok: true, entries: [entry] } as unknown as StoreSnapshot['history'] }), 'history');
    const words = text(markup);
    expect(words).toContain('Live');
    for (const old of ['LIVE RUNTIME', 'PHYSICAL', 'REHEARSAL', 'MOTION NOT RECORDED']) expect(words).not.toContain(old);
  });
});
