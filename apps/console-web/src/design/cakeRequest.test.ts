// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAPABILITY REQUEST IN THE CAKE PANEL, WITH THE SLOT READ BEFORE AND AFTER; THE FOCUSED DIAGNOSTICS DRAWER; AND
// THE CURRENT ACTION CARD SAID ONE FACT AT A TIME.
//
// The design review of record returned the capability request to the Cake panel's request slot, and the backend now
// records the capability slot as read just before an activation's submit and just after its answer. These pin, on the
// rendered screen:
//
//   the request sits in the Cake panel and not in the camera column, and is offered only while the mission waits;
//   the newest activation shows its phase, its result and the slot's state, capsule, generation and instance on both
//   sides of the operation, and `activationLine` stays the only place that decides what reads as activated;
//   an operation in flight turns the button off with that reason, and a rejected one leaves the mission held and the
//   button available again; a mission whose stop was asked for turns it off too, because the runtime submits nothing
//   for it (mission_stopping);
//   every activation the mission recorded keeps its transaction and its slot read-back in Evidence, so the first
//   one's is still on a screen after a second one replaces it in the Cake panel;
//   the header button and the drawer read Diagnostics, with the scope line, and nothing leads to Field bindings;
//   the rail says STALE when it dims;
//   and the observe-only LIVE action card no longer reads as one garbled paragraph.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import { SyntheticSource } from '../api/synthetic';
import type { MissionActivationRecord, MissionRecord, SlotReading } from '../model/missionRecord';
import { parseMission } from '../model/missionRecord';
import { applySnapshot, initialState } from '../model/reducer';
import { robotBody, robotStatus } from '../model/robotStatusFixture';
import type { CurrentAction, Readiness } from '../model/steps';
import type { OutcomeView, ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { actionCard } from './actionCard';
import { activationLine } from './cake';
import { Drawer } from './Drawers';
import { Screen } from './Screen';
import { C, INK } from './theme';

function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
}

const INERT = 'b9'.repeat(32);
const MOBILE = '87'.repeat(32);

function reading(capsule: string, generation: string, active: boolean): SlotReading {
  return {
    read: 'ok', slot_uuid: '5050', name: 'mobile_approach', state: 'ACTIVE', capsule, generation, instance: generation === '1' ? '3' : '4',
    session_uuid: '0a'.repeat(16), plan_digest: '8b'.repeat(32), epoch: generation === '1' ? '2' : '3', capability_active: active,
  };
}

function activation(extra: Partial<MissionActivationRecord>): MissionActivationRecord {
  return {
    operation_uuid: 'op-0001', target_id: 'cube_far', candidate_key: 'mobile_approach.activate', payload_digest: 'sha256:' + 'ab'.repeat(32),
    activation_state: 'SUBMITTED', outcome: null, transaction_phase: null, transaction_uuid: null, detail: null,
    t_wall_utc: '2026-09-24T10:05:00.000Z', ...extra,
  };
}

function held(activations: MissionActivationRecord[] = []): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-0000000000aa', state: 'WAITING_FOR_CAPABILITY', data_mode: 'synthetic',
    performs_motion: false, execution_label: 'recording-rehearsal', started_wall_utc: '2026-09-24T10:00:00.000Z', selected_target: 'cube_far',
    waiting_reason: 'capability_unavailable', detail: null,
    targets: [
      { target_id: 'cube_near', label: 'the reachable cube', reachable: true, requires_capability: false, state: 'PLACED', runs: [] },
      { target_id: 'cube_far', label: 'the far cube', reachable: false, requires_capability: true, state: 'WAITING_FOR_CAPABILITY', runs: [] },
    ],
    activations,
  };
}

/** A runtime with no run in progress. */
function idle(): ViewState {
  const snapshot = JSON.parse(JSON.stringify(runningSnapshot)) as Record<string, unknown>;
  snapshot['active_run'] = null;
  return applySnapshot(initialState(), snapshot as unknown as Snapshot);
}

function screen(patch: Partial<StoreSnapshot>, view: ViewState = idle()): string {
  const store = new ConsoleStore(new SyntheticSource());
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), sourceKind: 'live', ...patch, view };
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
}

const CANDIDATES = { missionCandidates: [{ key: 'mobile_approach.activate', payload_digest: 'sha256:' + 'ab'.repeat(32) }] };

describe('the capability request is in the Cake panel', () => {
  it('sits in the Cake panel request slot, after the robot rows, and not in the camera column', () => {
    const markup = screen({ mission: held(), ...CANDIDATES });
    const aside = markup.indexOf('<aside');
    const request = markup.indexOf('data-capability-request');
    expect(aside).toBeGreaterThan(-1);
    expect(request).toBeGreaterThan(aside);
    // The camera column is the <section> before the aside; the request is not in it.
    const section = markup.slice(markup.indexOf('<section'), aside);
    expect(section).not.toContain('data-capability-request');
    expect(text(markup)).toContain('Activate this candidate');
    expect(text(markup)).toContain('The capability request in the Cake panel is what lets the mission go on.');
  });

  it('is offered at no other time', () => {
    for (const state of ['RUNNING', 'COMPLETE', 'STOPPED', 'FAILED']) {
      expect(screen({ mission: { ...held(), state }, ...CANDIDATES })).not.toContain('data-capability-request');
    }
  });
});

describe('the slot read back, before and after', () => {
  it('shows the phase, the result and the capsule, generation, instance and state on both sides', () => {
    const observed = activation({
      activation_state: 'OBSERVED', outcome: 'accepted', transaction_phase: 'COMPLETED', transaction_uuid: 't-1',
      slot_before: reading(INERT, '1', false), slot_after: reading(MOBILE, '2', true),
      transaction: { operation_uuid: 'op-0001', transaction_uuid: 't-1', transaction_handle: '17', transaction_phase: 'COMPLETED', status: 'ok', reason_code: null, answer_epoch: null },
    });
    const markup = screen({ mission: { ...held([observed]), state: 'RUNNING', waiting_reason: null } });
    const readback = markup.slice(markup.indexOf('data-activation-readback'));
    const words = text(readback);
    expect(words).toContain('Activated');
    expect(words).toContain('phase COMPLETED');
    expect(words).toContain('result accepted');
    expect(words).toContain('transaction handle 17');
    expect(words).toContain(INERT);
    expect(words).toContain(MOBILE);
    expect(words).toMatch(/generation 1 2/);
    expect(words).toMatch(/instance 3 4/);
    expect(words).toMatch(/state ACTIVE ACTIVE/);
  });

  it('says a read that failed failed, and never draws it as an empty slot', () => {
    const failedRead: SlotReading = { ...reading(INERT, '1', false), read: 'failed', capsule: null, generation: null, instance: null, state: null };
    const markup = screen({ mission: held([activation({ activation_state: 'REQUEST_FAILED', slot_before: failedRead, slot_after: failedRead })]), ...CANDIDATES });
    expect(text(markup)).toContain('the read failed; this is not an empty slot');
    expect(text(markup)).toContain('not reported');
  });

  it('parses the read-back the runtime records, counts as the text they are', () => {
    const mission = parseMission({
      mission_id: 'mission-1', state: 'WAITING_FOR_CAPABILITY', stop_requested: true, records_after_end: 2,
      activations: [{ operation_uuid: 'op-1', activation_state: 'COMPLETED_NOT_OBSERVED', slot_before: { read: 'ok', generation: '18446744073709551615', capability_active: false },
                      slot_after: { read: 'ok', generation: 2, epoch: 'not-a-number' }, transaction: { transaction_handle: '9', answer_epoch: null }, stopped_during_activation: true }],
    });
    const parsed = mission?.activations[0];
    expect(parsed?.slot_before?.generation).toBe('18446744073709551615');
    expect(parsed?.slot_after?.generation).toBe('2');
    expect(parsed?.slot_after?.epoch).toBeNull();
    expect(parsed?.transaction?.transaction_handle).toBe('9');
    expect(parsed?.stopped_during_activation).toBe(true);
    expect(mission?.stop_requested).toBe(true);
    expect(mission?.records_after_end).toBe(2);
  });
});

describe('one operation at a time, and a rejection that can be recovered from', () => {
  it('turns the button off, with the reason on the page, while the newest operation is in flight', () => {
    const markup = screen({ mission: held([activation({ activation_state: 'SUBMITTED' })]), ...CANDIDATES });
    const button = /<button[^>]*data-control="activate"[^>]*>/.exec(markup)?.[0] ?? '';
    expect(button).toContain('disabled=""');
    expect(button).toContain('aria-disabled="true"');
    expect(text(markup)).toContain('is in flight (SUBMITTED)');
    expect(text(markup)).toContain('Submitted, not yet activated');
  });

  // THE COLOUR OF THE CLASSIC READBACK (9b34c09; verifier r2 test integrity 3): a refusal is never red or amber without an
  // expectation that says so, bright ink with none served, and ok only with the served expectation met and the slot unchanged.
  function readbackHeadline(markup: string): string {
    const at = markup.indexOf('data-activation-readback');
    expect(at).toBeGreaterThan(-1);
    const bold = markup.indexOf('font-weight:600', at);
    return markup.slice(markup.lastIndexOf('<span', bold), markup.indexOf('</span>', bold) + 7);
  }

  it('draws a Cake refusal with no expectation served in the neutral bright ink, never red or amber (verdict 2026-09-27 item 12)', () => {
    const markup = screen({ mission: held([activation({ activation_state: 'REJECTED_BY_CAKE', outcome: 'aborted', transaction_phase: 'ABORTED' })]), ...CANDIDATES });
    const headline = readbackHeadline(markup);
    expect(text(headline)).toContain('Cake refused the successor');
    expect(headline).toContain(`color:${INK.bright}`);
    expect(headline).not.toContain(C.bad);
    expect(headline).not.toContain(C.warn);
  });

  it('draws a Cake refusal that met its served expectation, the slot unchanged, in the ok tone', () => {
    const slot: SlotReading = { read: 'ok', slot_uuid: 's', name: 'mobile_approach', state: 'ACTIVE', capsule: 'aa'.repeat(32), generation: '1', instance: '7', session_uuid: 'x', plan_digest: null, epoch: null, capability_active: false };
    const refused = activation({ activation_state: 'REJECTED_BY_CAKE', outcome: 'aborted', transaction_phase: 'ABORTED', slot_before: slot, slot_after: slot });
    const served = { missionCandidates: [{ key: 'mobile_approach.activate', payload_digest: 'sha256:' + 'ab'.repeat(32), role: 'refused-demo', label: null, expect: 'REJECTED_BY_CAKE' }] };
    const headline = readbackHeadline(screen({ mission: held([refused]), ...served }));
    expect(text(headline)).toContain('Refused as expected');
    expect(headline).toContain(`color:${C.ok}`);
    expect(headline).not.toContain(C.bad);
  });

  it('leaves a rejected activation held and explained, and the rejection holds no button off', () => {
    const markup = screen({
      mission: held([activation({ activation_state: 'REJECTED_BY_CAKE', outcome: 'aborted', transaction_phase: 'ABORTED' })]),
      ...CANDIDATES,
    });
    const words = text(markup);
    expect(words).toContain('Held, waiting for a capability');
    expect(words).toContain('Cake refused the successor; the slot was not read both times');
    expect(words).not.toContain('The transaction aborted');
    expect(words).toContain('Nothing has failed');
    // NOTHING IS PRESELECTED (a deliberate change: the first sorted key was the abort-swap candidate, sent on the first
    // press). The button waits for the operator's choice, and that is the only thing keeping it off.
    const button = /<button[^>]*data-control="activate"[^>]*>/.exec(markup)?.[0] ?? '';
    expect(button).toContain('aria-disabled="true"');
    expect(words).toContain('Off: choose one of the prepared candidates first.');
    expect(words).not.toContain('is in flight');
  });

  it('preselects no candidate: the list opens on a choice, not on the first key', () => {
    const two = { missionCandidates: [{ key: 'abort_swap.demo', payload_digest: null }, { key: 'mobile_approach.activate', payload_digest: null }] };
    const markup = screen({ mission: held(), ...two });
    const select = markup.slice(markup.indexOf('data-control="candidate"'), markup.indexOf('</select>'));
    expect(select).not.toMatch(/<option[^>]*selected=""[^>]*value="abort_swap.demo"|<option[^>]*value="abort_swap.demo"[^>]*selected=""/);
    expect(select).toContain('choose a prepared candidate');
    expect(text(markup)).toContain('Off: choose one of the prepared candidates first.');
  });

  it('turns the button off, with the reason on the page, once the mission has been asked to stop', () => {
    const markup = screen({
      mission: { ...held([activation({ activation_state: 'REJECTED_BY_CAKE', outcome: 'aborted', transaction_phase: 'ABORTED' })]), stop_requested: true },
      ...CANDIDATES,
    });
    const button = /<button[^>]*data-control="activate"[^>]*>/.exec(markup)?.[0] ?? '';
    expect(button).toContain('disabled=""');
    expect(button).toContain('aria-disabled="true"');
    expect(text(markup)).toContain('Off: mission mission-0000000000aa is stopping');
    expect(text(markup)).toContain('(mission_stopping)');
  });

  it('never reads a completed transaction whose slot did not move as activated', () => {
    const line = activationLine(activation({ activation_state: 'COMPLETED_NOT_OBSERVED', transaction_phase: 'COMPLETED' }));
    expect(line.activated).toBe(false);
    expect(line.verdict).toBe('not_observed');
    expect(line.headline).toBe('Completed, capability not in the slot');
    expect(line.qualifier).toContain('A phase is not an activation');
  });
});

describe('every activation keeps its read-back', () => {
  // A rejected first activation, then an observed second one: the Cake panel moves on to the second, and the first's
  // transaction and slot read-back must still be on a screen (operator requirement: "retain the resulting transaction
  // and slot read-back").
  const first = activation({
    operation_uuid: 'op-0001', activation_state: 'REJECTED_BY_CAKE', outcome: 'aborted', transaction_phase: 'ABORTED', transaction_uuid: 't-1',
    slot_before: reading(INERT, '1', false), slot_after: reading(INERT, '1', false),
    transaction: { operation_uuid: 'op-0001', transaction_uuid: 't-1', transaction_handle: '11', transaction_phase: 'ABORTED', status: 'ok', reason_code: null, answer_epoch: '2' },
    stopped_during_activation: false,
  });
  const second = activation({
    operation_uuid: 'op-0002', activation_state: 'OBSERVED', outcome: 'accepted', transaction_phase: 'COMPLETED', transaction_uuid: 't-2',
    slot_before: reading(INERT, '1', false), slot_after: reading(MOBILE, '2', true),
    transaction: { operation_uuid: 'op-0002', transaction_uuid: 't-2', transaction_handle: '12', transaction_phase: 'COMPLETED', status: 'ok', reason_code: null, answer_epoch: '3' },
    stopped_during_activation: false,
  });
  const mission = { ...held([first, second]), state: 'RUNNING', waiting_reason: null };

  function evidence(): string {
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), mission };
    return renderToStaticMarkup(
      createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'evidence', snapshot, store, mode: 'synthetic', onSelect: () => {}, onClose: () => {} })),
    );
  }

  it('draws each record in Evidence with its handle, its answer epoch and the slot before and after', () => {
    const markup = evidence();
    for (const [operation, handle, epoch, capsuleAfter] of [
      ['op-0001', '11', '2', INERT],
      ['op-0002', '12', '3', MOBILE],
    ] as const) {
      const start = markup.indexOf(`data-activation-record="${operation}"`);
      expect(start, operation).toBeGreaterThan(-1);
      const end = markup.indexOf('data-activation-record=', start + 1);
      const words = text(markup.slice(start, end === -1 ? undefined : end));
      expect(words, operation).toContain(`transaction handle ${handle}`);
      expect(words, operation).toContain(`answer epoch ${epoch}`);
      expect(words, operation).toContain(INERT);
      expect(words, operation).toContain(capsuleAfter);
      expect(words, operation).toMatch(/slot before after read read back read back/);
      // The full record also says which resident answered each read, and under which Plan.
      expect(words, operation).toContain('0a'.repeat(16));
      expect(words, operation).toContain('8b'.repeat(32));
    }
    expect(text(markup)).toContain('Cake refused the successor');
    expect(text(markup)).not.toContain('The transaction aborted');
    expect(text(markup)).toContain('Activated');
  });

  it('points from the Cake panel, which shows the newest, to Evidence for the earlier one', () => {
    const words = text(screen({ mission }));
    expect(words).toContain('transaction handle 12');
    expect(words).toContain('The earlier activation of this mission, with its slot read-back, is in Evidence.');
  });
});

describe('the focused Diagnostics drawer', () => {
  it('is opened by a header button reading Diagnostics, and nothing leads to Field bindings', () => {
    const markup = screen({});
    expect(/<button[^>]*data-control="diagnostics"[^>]*>Diagnostics<\/button>/.test(markup)).toBe(true);
    expect(text(markup)).not.toContain('Field bindings');
  });

  it('is titled Diagnostics, with its four tabs and its scope line', () => {
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), mission: held() };
    const markup = renderToStaticMarkup(
      createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'history', snapshot, store, mode: 'synthetic', onSelect: () => {}, onClose: () => {} })),
    );
    expect(/data-drawer-title=""[^>]*>Diagnostics</.test(markup)).toBe(true);
    for (const tab of ['History', 'Logs', 'Evidence', 'Connections']) expect(markup).toContain(`>${tab}</button>`);
    expect(text(markup.slice(markup.indexOf('data-drawer-scope')))).toContain('mission: mission-0000000000aa');
    expect(text(markup)).not.toContain('Field bindings');
  });
});

describe('the rail when the robot stops answering', () => {
  it('carries the STALE tag as well as the dimming', () => {
    const lost = robotStatus({
      reachable: false,
      stale: true,
      reason_code: 'robot_unreachable',
      resident: { ...(robotBody().resident as object), reachable: false, stale: true, observed: { wall_utc: '2026-09-23T10:00:00Z', age_ms: 95_000 } },
    });
    const words = text(screen({ robot: lost, robotReadAtMs: Date.now(), robotError: null }));
    const rail = words.slice(words.indexOf('1 Find'), words.indexOf('Target'));
    expect(rail).toContain('STALE');
  });
});

describe('the current action on an observe-only LIVE runtime', () => {
  const ready: Readiness = { ready: false, blocker: 'the runtime was started without --enable-hardware-commands', code: 'hardware_commands_not_enabled', detail: null };
  const idleAction: CurrentAction = { phrase: 'Run finished', detail: null, state: null };
  const failed = { run_id: 'console-20260922T194137Z-gellh5f4', reported: 'failed' } as unknown as OutcomeView;

  it('is one truthful sentence per fact, with no clause pasted mid-sentence and no stray outcome word', () => {
    const card = actionCard({ robotStale: false, link: { state: 'answering', detail: '' }, waiting: null, running: false, result: failed, ready, action: idleAction });
    expect(card.title).toBe('Idle');
    expect(card.lines).toEqual([
      'No run is active.',
      "The newest run on this runtime's record has ended; its outcome, duration and evidence are in History.",
      'What stands in the way of a run: the runtime was started without --enable-hardware-commands.',
    ]);
    for (const line of card.lines) expect(line).toMatch(/^[A-Z].*\.$/);
  });

  it('draws no finished-run line on the main screen, so "LIVE RUNTIME. failed." cannot appear there', () => {
    const view = { ...idle(), data_mode: 'live' as const, outcome: { ...idle().outcome, run_id: 'run-9', reported: 'failed', finished_seq: 9 } } as ViewState;
    const words = text(screen({}, view));
    expect(words).not.toContain('LIVE RUNTIME. failed.');
    expect(words).not.toContain('History. the runtime');
    expect(words).toContain('its outcome, duration and evidence are in History.');
  });

  it('says in History the word the run ended with, rather than a bare "failed."', () => {
    // A failed run whose record establishes nothing more than its outcome word: no end predicates on it or in the fold.
    const view = {
      ...idle(),
      data_mode: 'live' as const,
      predicates: [],
      outcome: { ...idle().outcome, run_id: 'run-9', reported: 'failed', finished_seq: 9, predicates: null },
    } as ViewState;
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), sourceKind: 'live' as const, view };
    const words = text(
      renderToStaticMarkup(
        createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'history', snapshot, store, mode: 'live', onSelect: () => {}, onClose: () => {} })),
      ),
    );
    // The run's spec 5.1 word (browser r2 N2, verifier R-4), never its token and never a bare "failed.".
    expect(words).toContain('Live. The run ended: Failed.');
    expect(words).not.toContain('the outcome failed');
    expect(words).not.toContain('LIVE RUNTIME. failed.');
  });

  it('says nothing about a finished run when the runtime has none on record', () => {
    const card = actionCard({ robotStale: false, link: { state: 'answering', detail: '' }, waiting: null, running: false, result: null, ready, action: { phrase: 'Idle', detail: 'no cycle has been commanded in this runtime session', state: null } });
    expect(card.lines.join(' ')).not.toContain('History');
    expect(card.lines).toEqual([
      'No cycle has been commanded in this runtime session.',
      'What stands in the way of a run: the runtime was started without --enable-hardware-commands.',
    ]);
  });
});
