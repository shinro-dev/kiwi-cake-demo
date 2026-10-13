// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The panels an operator reads at a glance and acts with: the strip, the controls and Cake.
//
// Every case here is a sentence that was WRONG on the screen of a running stack, or a rule that keeps one from coming
// back. The strip printed the word unavailable twice in a row for one fact, called a simulation with no camera a
// degraded one, and put a green tick on "Cake not participating". The controls carried two global switches that
// changed what every button below them did, and a sample value in every manual box that would have executed if
// somebody had only pressed send.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { applySnapshot, initialState } from '../model/reducer';
import type { PredicateView, ViewState } from '../model/types';
import { SyntheticSource } from '../api/synthetic';
import { ConsoleStore } from '../store/store';
import { CakePanel, CAKE_ADAPTER_LABEL, CAKE_TASK_SENTENCE } from './SystemPanels';
import { Actions } from './Actions';
import { StatusStrip, modeBanner, replayPosition } from './StatusStrip';
import { cameraReadouts, connectivityReadouts, consoleReadout, executionBackend, robotReadout } from './connectivity';
import {
  authorityBlock,
  bindingInvalidation,
  deriveHolding,
  entered,
  enteredSigned,
  previewRefusal,
  readPending,
  rememberPending,
  startRefusal,
} from './commandGuards';
import type { PreviewBinding } from './commandGuards';

const NOW = 5_000_000;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

/** Every button on a rendered panel, with the words on it and whether it is off. */
function buttons(markup: string): { label: string; disabled: boolean }[] {
  return [...markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map((match) => ({
    label: text(match[2] ?? '').trim(),
    disabled: (match[1] ?? '').includes('disabled'),
  }));
}

function running(): ViewState {
  return applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
}

function snapshotWith(patch: Record<string, unknown>): ReturnType<ConsoleStore['getSnapshot']> {
  const store = new ConsoleStore(new SyntheticSource());
  return { ...store.getSnapshot(), ...patch } as ReturnType<ConsoleStore['getSnapshot']>;
}

function predicate(name: string, value: boolean | null, seq: number): PredicateView {
  return {
    predicate: name,
    value,
    state: null,
    reason_code: null,
    rule_ref: null,
    rule_digest: null,
    threshold: null,
    measured: null,
    unit: null,
    evidence: [],
    consumed_source_stamp: null,
    measurements: [],
    seq,
    t_mono_ns: 0,
    clock_domain: 'laptop_monotonic',
  };
}

describe('connectivity is asked as three questions and never answered with one badge', () => {
  it('says the console is reading while the runtime reports no robot host at all', () => {
    // The exact arrangement on the screen of a running stack: lines arriving several times a second, under a single
    // badge that read "unknown" because the runtime holds no link to a robot it does not have.
    const snapshot = snapshotWith({
      view: { ...running(), link: { state: 'unknown', source_stamp: null, receive_age_ms: null }, runtime_session: 'session-a' },
      telemetry: { tap: null, preview: null, tap_received_at_ms: NOW - 200, preview_received_at_ms: null },
    });
    expect(consoleReadout(snapshot).words).toBe('lines are arriving');
    const robot = robotReadout(snapshot, NOW);
    expect(robot.words).toBe('no host observation yet');
    expect(robot.detail).toContain('the state that mode is expected to be in');
  });

  it('does not call an absent camera in a simulation a degraded one', () => {
    const snapshot = snapshotWith({ view: running() });
    const rows = cameraReadouts(snapshot, NOW);
    // The running fixture names a wrist camera the runtime reports as degraded and a front camera it has nothing on.
    expect(rows.map((row) => row.label)).toContain('wrist');
    for (const row of rows) {
      if (row.words === 'nothing captured yet') expect(row.detail).toContain('the state that mode is expected to be in');
    }
  });

  it('gives every camera the runtime names a readout of its own', () => {
    const view = running();
    const snapshot = snapshotWith({
      view: {
        ...view,
        components: [
          ...view.components,
          { id: 'front-camera', layer: 'device', role: 'the front camera', health: 'ok', active: true, version: null, evidence: 'two captures' },
        ],
      },
    });
    const rows = cameraReadouts(snapshot, NOW);
    expect(rows.filter((row) => row.kind === 'camera').map((row) => row.label).sort()).toEqual(['front', 'wrist']);
  });

  it('says once that nothing is connected rather than nothing at all', () => {
    const snapshot = snapshotWith({ view: { ...initialState(), components: [] } });
    const rows = connectivityReadouts(snapshot, NOW);
    expect(rows.map((row) => row.id)).toEqual(['console', 'robot', 'camera:none']);
    for (const row of rows) expect(row.words, row.id).not.toBe('');
  });
});

describe('the artifact that is executing, named from a record that named it', () => {
  it('says so in one sentence when no record has named a producer, and never the word unavailable twice', () => {
    const backend = executionBackend(snapshotWith({ view: initialState() }));
    expect(backend.name).toBeNull();
    expect(backend.basis).toBeNull();
    // The executing artifact is no longer summarised on the strip; the sentence it produced is asserted here, on the
    // model that produces it, and the engineering view is what prints it.
    expect(executionBackend(snapshotWith({ view: initialState() })).basis).toBeNull();
  });

  it('keeps naming the producer a record named earlier, after the last record carried none', () => {
    const view = running();
    const named = {
      schema_version: '1',
      seq: 10,
      run_id: null,
      action_id: null,
      observation_id: null,
      producer: { name: 'single-cube-controller', version: '0.4.0', artifact_digest: 'sha256:0123456789abcdef', contract_version: '1' },
      source_session: 'session-a',
      t_mono_ns: 1,
      clock_domain: 'laptop_monotonic',
      t_wall_utc: null,
      reason_code: null,
      provenance: null,
      kind: 'state.transition',
      payload: {},
    };
    const anonymous = { ...named, seq: 11, producer: { name: '', version: '', artifact_digest: null, contract_version: '1' } };
    const backend = executionBackend(
      snapshotWith({ view: { ...view, recent_events: [named, anonymous] as unknown as ViewState['recent_events'] } }),
    );
    expect(backend.name).toBe('single-cube-controller');
    expect(backend.basis).toContain('seq 10');
  });

  it('falls back to the producer sessions of the fold when no record is left to read', () => {
    const view = running();
    const backend = executionBackend(
      snapshotWith({
        view: { ...view, recent_events: [], source_sessions: [{ producer: 'demo-runtime', session: 'session-a', first_seq: 1, last_seq: 9, records: 9 }] },
      }),
    );
    expect(backend.name).toBe('demo-runtime');
    expect(backend.version).toBeNull();
    expect(backend.basis).toContain('session-a');
  });
});

describe('the strip, rendered', () => {
  function strip(view: ViewState, patch: Record<string, unknown> = {}): string {
    return renderToStaticMarkup(
      createElement(StatusStrip, {
        snapshot: snapshotWith({ view, ...patch }),
        mode: view.data_mode,
        onStop: () => undefined,
        stopBusy: false,
        nowMs: NOW,
      }),
    );
  }

  // The Cake row and the task-outcome row were removed from the strip. Cake is stated once, beside the modules, and
  // the run's outcome is in the rail, scoped to the run it belongs to. The colour rule for Cake is still held, on the
  // panel that draws it, in 'Cake is beside the task' below.

  it('draws the console, the robot and each camera as readouts of their own', () => {
    const words = text(strip(running(), { telemetry: { tap: null, preview: null, tap_received_at_ms: NOW - 300, preview_received_at_ms: null } }));
    expect(words).toContain('console: lines are arriving');
    expect(words).toContain('robot telemetry:');
    expect(words).toContain('wrist:');
  });

});

describe('the banner that says what the screen is', () => {
  it('never lets the script inside the page pass as a live runtime', () => {
    // The script decides the words it appears under, whatever data_mode its own snapshot happens to carry.
    expect(modeBanner('live', 'synthetic')).toContain('No runtime, no robot and no camera were involved');
  });
});

describe('a recording carries its own time and its own provenance', () => {
  it('shows where the cursor is, when that record was written and the mode the RECORDING states', () => {
    const record = {
      schema_version: '1',
      seq: 4,
      run_id: 'sc-20260101T000000Z-abcd1234',
      action_id: null,
      observation_id: null,
      producer: { name: 'single-cube-controller', version: '0.4.0', artifact_digest: null, contract_version: '1' },
      source_session: 'session-a',
      t_mono_ns: 1,
      clock_domain: 'laptop_monotonic',
      t_wall_utc: '2026-01-01T00:00:04Z',
      reason_code: null,
      provenance: null,
      kind: 'state.transition',
      payload: {},
    };
    const snapshot = snapshotWith({
      view: running(),
      replay: { run_id: 'sc-20260101T000000Z-abcd1234', events: [record, record] as unknown as never[], cursor: 1, origins: [] },
      history: {
        ok: true,
        entries: [{ run_id: 'sc-20260101T000000Z-abcd1234', data_mode: 'synthetic' } as unknown as never],
      },
    });
    const position = replayPosition(snapshot);
    expect(position?.folded).toBe(1);
    expect(position?.total).toBe(2);
    expect(position?.wall_utc).toBe('2026-01-01T00:00:04Z');
    expect(position?.recorded_mode).toBe('synthetic');
  });

  it('says the recording does not state a mode rather than borrowing the runtime it is being replayed on', () => {
    const snapshot = snapshotWith({
      view: { ...running(), data_mode: 'live' },
      replay: { run_id: 'a-run', events: [] as unknown as never[], cursor: 0, origins: [] },
      history: { ok: true, entries: [{ run_id: 'a-run' } as unknown as never] },
    });
    expect(replayPosition(snapshot)?.recorded_mode).toBeNull();
  });
});

describe('Cake is beside the task, and not participating is never a pass', () => {
  function cakeView(value: boolean | null): ViewState {
    const view = running();
    return {
      ...view,
      cake: {
        ...view.cake,
        ladder: { ...view.cake.ladder, mission_participation: { value, evidence: 'synthetic: nothing connects this console to a resident today' } },
      },
    };
  }

  it('states the sentence this demo states about Cake, exactly', () => {
    const words = text(renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false) })));
    expect(words).toContain(CAKE_TASK_SENTENCE);
  });

  it('never draws not participating in the colours of a passing check', () => {
    const markup = renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false) }));
    // The one tone this screen uses for a pass is badge--ok. The rungs of this fixture are all false or not
    // determined, so nothing on this card may carry it.
    expect(markup).not.toContain('badge--ok');
    expect(text(markup)).toContain('Not connected to this task');
  });

  it('offers every physical update control disabled, labelled with what it is waiting for', () => {
    const markup = renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false) }));
    const adapter = buttons(markup).filter((button) => button.label === CAKE_ADAPTER_LABEL);
    expect(adapter).toHaveLength(6);
    for (const button of adapter) expect(button.disabled).toBe(true);
    // The server's own reason stays beside the control rather than being replaced by the label.
    expect(text(markup)).toContain('no resident is reachable from this console');
  });

  it('reads the sentence from the mission record while a mission waits on, or resumed through, a Cake activation', () => {
    const base = {
      mission_record_version: '1', mission_id: 'mission-0001', state: 'WAITING_FOR_CAPABILITY', data_mode: 'live', performs_motion: true,
      execution_label: 'physical', started_wall_utc: null, selected_target: 'cube', waiting_reason: 'capability_unavailable', detail: null,
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto' as const, state: 'WAITING_FOR_CAPABILITY', runs: [] }],
      activations: [],
    };
    const held = text(renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false), mission: base })));
    expect(held).toContain('held for a capability');
    expect(held).not.toContain(CAKE_TASK_SENTENCE);
    const resumed = text(
      renderToStaticMarkup(
        createElement(CakePanel, {
          view: cakeView(false),
          mission: {
            ...base,
            state: 'RUNNING',
            waiting_reason: null,
            activations: [
              { operation_uuid: 'op-1', target_id: 'cube', candidate_key: 'mobile-demo-swapped', payload_digest: null, activation_state: 'OBSERVED', outcome: null, transaction_phase: null, transaction_uuid: null, detail: null, t_wall_utc: null },
            ],
          },
        }),
      ),
    );
    expect(resumed).toContain('resumed through a Cake activation');
    // A mission that never touched Cake still gets the constant: it is true of that mission.
    const untouched = text(renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false), mission: { ...base, state: 'RUNNING', waiting_reason: null } })));
    expect(untouched).toContain(CAKE_TASK_SENTENCE);
  });

  it('points the module update control at the capability slot check card, which is the live update this demo performs', () => {
    const words = text(renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false) })));
    expect(words).toContain('capability slot check card in the Cake panel');
  });

  it('keeps a reachable resident, a slot existing and a task using one as three separate facts', () => {
    const words = text(renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false) })));
    expect(words).toContain('a resident this runtime could reach');
    expect(words).toContain('slots the resident reported');
    expect(words).toContain('a task using those slots');
    expect(words).toContain('never infers a resident from a process table');
  });

  it('states four absences once instead of drawing four empty tables', () => {
    const words = text(renderToStaticMarkup(createElement(CakePanel, { view: cakeView(false) })));
    expect(words).toContain('No slot, transaction, update preview or continuity measurement has been reported');
  });
});

describe('holding is derived where the record settles it and left unknown where it does not', () => {
  it('reads the cube as held from the retention verdict', () => {
    const view = { ...running(), predicates: [predicate('object_secured', true, 40)] };
    const reading = deriveHolding(view);
    expect(reading.value).toBe(true);
    expect(reading.basis).toContain('object_secured true at seq 40');
  });

  it('reads the cube as let go once the controller has released after that verdict', () => {
    const view: ViewState = {
      ...running(),
      predicates: [predicate('object_secured', true, 40)],
      machine: { machine: 'cycle', state: 'RELEASED', from_state: 'RELEASE_ADMISSION', since_seq: 55, history: [] },
    };
    const reading = deriveHolding(view);
    expect(reading.value).toBe(false);
    expect(reading.basis).toContain('RELEASED at seq 55');
  });

  it('leaves holding unknown when nothing in the record settles it, and never resolves it to false', () => {
    expect(deriveHolding(running())).toEqual({ value: null, basis: null });
  });

  // THE RUNTIME'S OWN DEFINITION (demo_runtime/mission_execution.py, stop_state_from_events): the jaws closed on
  // something (closed_on_something true) or object_secured true, until a RELEASED, or a GRASP_SET_DOWN whose jaws then
  // measured open; a GRASP_CLOSE with no verdict after it is unknown.
  it('reads the cube as held when the jaws closed on something, whatever the retention verdict said after it', () => {
    const view = { ...running(), predicates: [predicate('closed_on_something', true, 30), predicate('object_secured', false, 40)] };
    const reading = deriveHolding(view);
    expect(reading.value).toBe(true);
    expect(reading.basis).toContain('closed_on_something true at seq 30');
  });

  it('reads the cube as let go once it was set down and the jaws measured open after that', () => {
    const view: ViewState = {
      ...running(),
      predicates: [predicate('closed_on_something', true, 30), predicate('gripper_opened', true, 60)],
      machine: { machine: 'cycle', state: 'GRASP_SET_DOWN', from_state: 'GRASP_VERIFY', since_seq: 50, history: [{ state: 'GRASP_CLOSE', seq: 20 }] },
    };
    const reading = deriveHolding(view);
    expect(reading.value).toBe(false);
    expect(reading.basis).toContain('gripper_opened true at seq 60');
  });

  it('leaves a close with no verdict after it unknown', () => {
    const view: ViewState = { ...running(), machine: { machine: 'cycle', state: 'GRASP_CLOSE', from_state: 'STRADDLING', since_seq: 50, history: [] } };
    expect(deriveHolding(view).value).toBeNull();
  });

  const attemptMission = (holding: boolean | null) =>
    ({
      mission_record_version: '1', mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
      started_wall_utc: '2026-09-27T14:16:38.847Z', selected_target: 'cube', waiting_reason: null, detail: null,
      targets: [{
        target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: ['console-20260927T142146Z-m6ed2dd9'],
        attempts: [{
          attempt: 1, run_id: 'console-20260927T142146Z-m6ed2dd9', park_run_id: null, outcome_class: 'stopped', reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE',
          measurements: [], observation_ids: [], stop_state: { holding, base_ahead: false, moved: true }, next_actions: [], budget_left: null, t_wall_utc: '2026-09-27T14:24:40.265Z',
        }],
      }],
      activations: [],
    }) as unknown as Parameters<typeof deriveHolding>[1];

  it("reads the mission's newest attempt when this tab folded none of the run's records (verifier r2 defect 2)", () => {
    const reading = deriveHolding(initialState(), attemptMission(true));
    expect(reading.value).toBe(true);
    expect(reading.basis).toContain('attempt 1');
    expect(reading.basis).toContain('console-20260927T142146Z-m6ed2dd9');
    expect(deriveHolding(initialState(), attemptMission(false)).value).toBe(false);
  });

  it("leaves holding unknown when the newest attempt records it unknown, and says the record left it unknown", () => {
    const reading = deriveHolding(initialState(), attemptMission(null));
    expect(reading.value).toBeNull();
    expect(reading.basis).toContain('attempt 1');
  });

  // NEWEST RECORD BY TIME: the attempt is the runtime's own verdict over every event of its run, so it wins over this
  // tab's fold of that same run (which keeps only the newest verdict of each predicate). Only a run this fold saw begin
  // after the attempt was written is newer than it.
  it("prefers the attempt over this tab's fold of the same run", () => {
    const view: ViewState = {
      ...running(),
      predicates: [predicate('closed_on_something', false, 80)],
      run_started: { command_id: 'c-1', command_type: 'start_cycle', plan_only: false, holding: null, started_wall_utc: '2026-09-27T14:21:46.000Z', run_id: 'console-20260927T142146Z-m6ed2dd9', seq: 1 },
    };
    const reading = deriveHolding(view, attemptMission(true));
    expect(reading.value).toBe(true);
    expect(reading.basis).toContain('attempt 1');
  });

  it('prefers the fold of a run that began after the attempt was written', () => {
    const view: ViewState = {
      ...running(),
      predicates: [predicate('closed_on_something', true, 30)],
      machine: { machine: 'cycle', state: 'RELEASED', from_state: 'RELEASE_ADMISSION', since_seq: 55, history: [] },
      run_started: { command_id: 'c-2', command_type: 'start_cycle', plan_only: false, holding: null, started_wall_utc: '2026-09-27T14:30:00.000Z', run_id: 'console-20260927T143000Z-later', seq: 1 },
    };
    const reading = deriveHolding(view, attemptMission(true));
    expect(reading.value).toBe(false);
    expect(reading.basis).toContain('RELEASED at seq 55');
  });

  it('draws no holding question beside Observe when the mission record settles it, and states the reading with its basis', () => {
    const view: ViewState = {
      ...initialState(),
      data_mode: 'live',
      capabilities: [{ id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }],
    };
    const snapshot = { ...snapshotWith({ view }), mission: attemptMission(true) } as ReturnType<ConsoleStore['getSnapshot']>;
    const markup = renderToStaticMarkup(createElement(Actions, { snapshot, store: new ConsoleStore(new SyntheticSource()), only: ['observe'] }));
    const words = text(markup);
    expect(words).not.toContain('Nothing in the record says');
    expect(words).not.toContain('Off until you state');
    expect(markup).toMatch(/data-control="observe"[^>]*aria-disabled="false"/);
    // Spec 5.7.1's words for stop_state.holding true, and the record it rests on.
    expect(words).toContain('Sent as: Holding the block');
    expect(words).toContain('attempt 1');
    // A control the runtime turned off sends nothing, so it says nothing about what it would send.
    const off: ViewState = { ...view, capabilities: [{ id: 'observe', state: 'disabled', reason_code: 'mission_running', reason: 'a mission is being carried', dependency: null, planned: false }] };
    const offSnapshot = { ...snapshotWith({ view: off }), mission: attemptMission(true) } as ReturnType<ConsoleStore['getSnapshot']>;
    const offWords = text(renderToStaticMarkup(createElement(Actions, { snapshot: offSnapshot, store: new ConsoleStore(new SyntheticSource()), only: ['observe'] })));
    expect(offWords).not.toContain('Sent as');
  });
});

describe('authority can only turn motion off, and only on what the runtime itself states', () => {
  const lease = {
    held: true,
    generation: 2,
    host_session: 'host-session-a',
    expires_in_ms: 800,
    reason: null,
    required: true,
    attached: true,
    renew_failures: 0,
    run_id: null,
    adopted: null,
    last_reply_age_ms: 120,
  };

  it('says nothing at all when the runtime reports no lease, which is what a simulation reports', () => {
    expect(authorityBlock(running())).toBeNull();
  });

  it('says nothing when the runtime states that this mode requires none', () => {
    expect(authorityBlock({ ...running(), lease: { ...lease, required: false, held: false } })).toBeNull();
  });

  it('refuses motion when a lease is required and the runtime holds none', () => {
    const reason = authorityBlock({ ...running(), lease: { ...lease, held: false, reason: 'owner_not_free' } });
    expect(reason).toContain('requires a host command lease');
    expect(reason).toContain('owner_not_free');
  });

  it('refuses motion when the host has not answered for longer than this console counts as current, without a ticking age', () => {
    const reason = authorityBlock({ ...running(), lease: { ...lease, last_reply_age_ms: 9000 } });
    expect(reason).toBe('the lease is held and the host has not answered within the 4000 ms this console counts as current');
    // The next snapshot's age gives the same words: nothing here changes while the host stays silent (spec 6.8).
    expect(authorityBlock({ ...running(), lease: { ...lease, last_reply_age_ms: 12000 } })).toBe(reason);
    expect(reason).not.toMatch(/\bago\b|9000/);
  });
});

describe('a preview is bound to what it was taken against', () => {
  const taken: PreviewBinding = {
    runtime_session: 'session-a',
    owner_kind: 'free',
    owner_run_id: null,
    lease_generation: 2,
    machine_state: 'SEARCH',
    parameters: 'deg -3.5',
  };

  it('is invalidated by an edited parameter, by another runtime, by another owner and by another state', () => {
    expect(bindingInvalidation(taken, { ...taken, parameters: 'deg -4.0' })).toContain('parameters were edited');
    expect(bindingInvalidation(taken, { ...taken, runtime_session: 'session-b' })).toContain('not the one the preview was taken against');
    expect(bindingInvalidation(taken, { ...taken, owner_kind: 'autonomous' })).toContain('who holds the robot changed');
    expect(bindingInvalidation(taken, { ...taken, machine_state: 'ALIGN' })).toContain('left the state the preview was planned from');
    expect(bindingInvalidation(taken, { ...taken })).toBeNull();
  });

  it('refuses to execute against nothing, against a refusal, and against a preview that has gone stale', () => {
    expect(previewRefusal(null, taken, NOW)).toContain('preview the plan first');
    const rejected = { command_id: 'abc', status: 'rejected', reason_code: 'step_out_of_bounds', payload_digest: null, at_ms: NOW, binding: taken };
    expect(previewRefusal(rejected, taken, NOW)).toContain('step_out_of_bounds');
    const accepted = { command_id: 'abc', status: 'accepted', reason_code: null, payload_digest: null, at_ms: NOW - 200_000, binding: taken };
    expect(previewRefusal(accepted, taken, NOW)).toContain('is not treated as current');
    expect(previewRefusal({ ...accepted, at_ms: NOW }, taken, NOW)).toBeNull();
  });
});

describe('a second start is refused in this browser before a request is built', () => {
  it('refuses while the first start has not been answered', () => {
    const reason = startRefusal([{ command_id: 'abcdefghij0', type: 'start_cycle', submitted_at: 0, result: null, unresolved: null }]);
    expect(reason).toContain('has not been answered yet');
  });

  it('refuses while the first start is one whose execution is not known', () => {
    const reason = startRefusal([{ command_id: 'abcdefghij0', type: 'start_cycle', submitted_at: 0, result: null, unresolved: 'the response was lost' }]);
    expect(reason).toContain('NOT known');
  });

  it('allows the next one once the first has an answer', () => {
    const result = { command_id: 'abcdefghij0', status: 'finished' as const, reason_code: null, run_id: null };
    expect(startRefusal([{ command_id: 'abcdefghij0', type: 'start_cycle', submitted_at: 0, result, unresolved: null }])).toBeNull();
  });
});

describe('what this browser remembers across a reload', () => {
  function fakeStorage(): Storage {
    const map = new Map<string, string>();
    return {
      get length() {
        return map.size;
      },
      clear: () => map.clear(),
      getItem: (key: string) => map.get(key) ?? null,
      key: (index: number) => [...map.keys()][index] ?? null,
      removeItem: (key: string) => map.delete(key),
      setItem: (key: string, value: string) => void map.set(key, value),
    } as Storage;
  }

  it('keeps the command id and the runtime it was sent to, and nothing else', () => {
    const storage = fakeStorage();
    rememberPending(storage, { command_id: 'abcdefghij0', command_type: 'start_cycle', runtime_session: 'session-a' });
    const raw = storage.getItem('kiwi-console-pending-command') ?? '';
    expect(Object.keys(JSON.parse(raw) as Record<string, unknown>).sort()).toEqual(['command_id', 'command_type', 'runtime_session']);
  });

  it('drops a remembered id that belongs to a runtime session that is not the one answering now', () => {
    const storage = fakeStorage();
    rememberPending(storage, { command_id: 'abcdefghij0', command_type: 'start_cycle', runtime_session: 'session-a' });
    expect(readPending(storage, 'session-b')).toBeNull();
    expect(readPending(storage, 'session-a')?.command_id).toBe('abcdefghij0');
  });

  it('carries on without storage at all rather than failing a send', () => {
    rememberPending(null, { command_id: 'abcdefghij0', command_type: 'start_cycle', runtime_session: null });
    expect(readPending(null, null)).toBeNull();
  });
});

describe('no manual control carries a value that would execute if somebody only pressed send', () => {
  it('reports an empty box as a missing value, with the unit and the bound', () => {
    expect(entered('', 'deg', -6, 6, true).missing).toContain('no value has been entered');
    expect(entered('', 'deg', -6, 6, true).value).toBeNull();
    expect(enteredSigned('', 's', 0.2, 0.8).missing).toContain('either way');
  });

  it('refuses a step outside the controller bound and a step of zero', () => {
    expect(entered('9', 'deg', -6, 6, true).missing).toContain('outside the controller bound');
    expect(entered('0', 'deg', -6, 6, true).missing).toContain('zero is not a step');
    expect(enteredSigned('0.1', 's', 0.2, 0.8).missing).toContain('outside the validated range');
    expect(enteredSigned('-0.6', 's', 0.2, 0.8).value).toBe(-0.6);
  });
});

describe('the primary actions, which are the only things this screen commands', () => {
  const store = new ConsoleStore(new SyntheticSource());

  it('offers the three direct labels and nothing else', () => {
    const markup = renderToStaticMarkup(createElement(Actions, { snapshot: snapshotWith({ view: running() }), store }));
    const labels = buttons(markup).map((button) => button.label);
    expect(labels).toContain('Observe scene');
    expect(labels).toContain('Start pick & place');
    expect(labels).toContain('Return arm');
    // The bounded manual jog controls were removed with the panel they lived on: this screen runs the proven cycle
    // and does not drive joints. Their capability ids stay in MOTION_CONTROLS, so the block still covers them.
    expect(labels).not.toContain('Base pulse');
    expect(text(markup)).not.toContain('show advanced actions');
  });

  it('offers no start at all while a recorded run is on the screen', () => {
    const markup = renderToStaticMarkup(
      createElement(Actions, {
        snapshot: snapshotWith({ view: running(), replay: { run_id: 'a-run', events: [] as unknown as never[], cursor: 0, origins: [] } }),
        store,
      }),
    );
    expect(buttons(markup).find((button) => button.label === 'Start pick & place')?.disabled).toBe(true);
    expect(text(markup)).toContain('a recorded run is on the screen');
  });

  it('turns every action off, with the block reason on it, while one command is unresolved', () => {
    const view = running();
    const blocked: ViewState = {
      ...view,
      unresolved: {
        command_id: 'abcd1234ef0123456789',
        run_id: null,
        command_type: 'start_cycle',
        reason_code: 'runtime_restarted_before_child_recorded',
        classification: null,
        block_reason_code: 'owner_unresolved',
        question: null,
        findings: [],
        evidence: null,
        attestations: [],
        blocked_commands: ['start_cycle'],
      },
    };
    const markup = renderToStaticMarkup(createElement(Actions, { snapshot: snapshotWith({ view: blocked }), store }));
    expect(text(markup)).toContain('owner_unresolved');
    for (const button of buttons(markup)) {
      expect(button.disabled, button.label).toBe(true);
    }
  });

  it('asks about holding once, at observe, and leaves observe off until it is settled', () => {
    // The server has to be offering observe before holding is the thing standing in the way: a capability the runtime
    // turned off is refused with the runtime's own reason and nothing is asked at all, which is the right order.
    const view = running();
    const offered: ViewState = {
      ...view,
      capabilities: [{ id: 'observe', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }],
    };
    const markup = renderToStaticMarkup(createElement(Actions, { snapshot: snapshotWith({ view: offered }), store }));
    const words = text(markup);
    expect(words).toContain('No record read here says whether the gripper is holding the cube');
    // Asked in ONE place, not beside every control.
    expect([...words.matchAll(/State it once/g)]).toHaveLength(1);
    expect(buttons(markup).find((button) => button.label === 'Observe scene')?.disabled).toBe(true);
  });
});
