// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// DIAGNOSTICS NEVER CONTRADICTS THE DEMO SCREEN (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 5.1 and
// 5.4). The History, Evidence, Connections and Cake tabs say the mission's and the runs' verdicts, the activations'
// verdicts and colours, the mission's finish time and the signature check's session in the demo screen's words, print
// every time in UTC with a Z, and count no age against this display's clock.
//
// The record is the one of 2026-09-27 on 8771 (mission-f65c3cf8e99f): run 1 needed the capability, the abort-swap was
// refused 1 -> 1 with no expectation served, the swap activated 1 -> 2, run 2 was stopped by the controller.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { HistoryEntry } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import { buildStoppedHoldingRun } from '../api/syntheticScenarios';
import type { ContractEvent } from '../contracts/envelope';
import { composeTimeline } from '../model/missionTimeline';
import { outcomeCard } from '../model/outcomeCard';
import type { MissionActivationRecord, MissionRecord, SlotReading } from '../model/missionRecord';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import { foldEvents, initialState } from '../model/reducer';
import { robotStatus } from '../model/robotStatusFixture';
import { placedRun } from '../model/stageFixtures';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { Drawer } from './Drawers';
import type { DrawerId } from './Drawers';
import { fixtureCheckAnswer } from './cakeFixtures';
import { element } from './demoScenarioPlay';
import designCss from './design.css?raw';
import baseCss from '../styles/base.css?raw';
import consoleCss from '../styles/console.css?raw';
import engineeringCss from '../styles/engineering.css?raw';
import sourcesCss from '../styles/sources.css?raw';
import type { ScreenMode } from '../panels/StatusStrip';

function storeShowing(view: ViewState, patch: Partial<StoreSnapshot> = {}): ConsoleStore {
  const store = new ConsoleStore(new SyntheticSource(), { storage: null });
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view };
  return store;
}

function drawer(store: ConsoleStore, id: DrawerId, mode: ScreenMode = 'live'): string {
  return renderToStaticMarkup(
    createElement(StoreContext, { value: store }, createElement(Drawer, { id, snapshot: store.getSnapshot(), store, mode, onSelect: () => undefined, onClose: () => undefined })),
  );
}

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/\s+/g, ' ');
}

const OLD = '9600566de6c9a4cee25afaa14ff0d3e403515c181f1f345da906c73085c9389e';
const NEW = '2f34a84c19719a6ef4a040fbbd8114d831d093ef81d6a9fb78240dc8696da8a7';

function slot(generation: string, capsule: string, instance: string): SlotReading {
  return { read: 'ok', slot_uuid: 's', name: 'mobile_approach', state: 'ACTIVE', capsule, generation, instance, session_uuid: '01a0e337', plan_digest: null, epoch: '2', capability_active: null };
}

const REFUSAL: MissionActivationRecord = {
  operation_uuid: '40b94b8a4bc742b89f4fe56b64e56376', target_id: 'cube', candidate_key: 'mobile-demo-active-successor-abort-swap', payload_digest: null,
  activation_state: 'REJECTED_BY_CAKE', outcome: 'REJECTED_BY_CAKE', transaction_phase: 'ABORTED', transaction_uuid: 'aa29',
  detail: 'the resident answered ABORTED; the transaction did not complete, and the answer does not say why', t_wall_utc: '2026-09-27T14:20:21.636Z',
  slot_before: slot('1', OLD, '600'), slot_after: slot('1', OLD, '600'),
};
const SWAP: MissionActivationRecord = { ...REFUSAL, operation_uuid: '5d293b0225f64a23b387bac05beeb2d8', candidate_key: 'mobile-demo-swapped', activation_state: 'OBSERVED', detail: 'the transaction completed', t_wall_utc: '2026-09-27T14:21:45.496Z', slot_after: slot('2', NEW, '601') };

const RUN_1 = 'console-20260927T141640Z-m1aaaaaa';
const RUN_2 = 'console-20260927T142146Z-m6ed2dd9';

const MISSION: MissionRecord = {
  mission_record_version: '1', mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
  started_wall_utc: '2026-09-27T14:16:38.847Z', selected_target: 'cube', waiting_reason: null, detail: 'run_outcome:stopped:base_not_zero_before_segment',
  stop_requested: false, stop_requested_wall_utc: null,
  targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: [RUN_1, RUN_2] }],
  activations: [REFUSAL, SWAP], hold: null, next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
};

function entry(runId: string, started: string, finished: string, outcome: string, exit: number): Record<string, unknown> {
  return {
    run_id: runId, command_id: null, command_type: 'start_cycle', started_wall_utc: started, finished_wall_utc: finished, outcome, exit_status: exit,
    evidence_complete: true, evidence_gaps: 0, artifact_digest: null, adopted: false, data_mode: 'live', bundle_digest: null,
  };
}

const HISTORY = {
  ok: true,
  entries: [entry(RUN_1, '2026-09-27T14:16:40.100Z', '2026-09-27T14:17:02.300Z', 'failed', 5), entry(RUN_2, '2026-09-27T14:21:46.200Z', '2026-09-27T14:24:40.114Z', 'stopped', 4)],
} as unknown as StoreSnapshot['history'];

const LIVE: ViewState = { ...placedRun('p'), data_mode: 'live' };
const BAD = '#f87171';

function withMission(patch: Partial<StoreSnapshot> = {}): ConsoleStore {
  return storeShowing(LIVE, { mission: MISSION, missionError: null, history: HISTORY, missionCandidates: [], ...patch });
}

describe("the activations in Diagnostics, in the demo screen's words and colours (spec 5.1, audit C10)", () => {
  for (const id of ['history', 'evidence'] as const) {
    it(`${id}: the refusal with no expectation served reads "Cake refused the successor, slot unchanged 1 -> 1", never red`, () => {
      const markup = drawer(withMission(), id);
      const row = element(markup, `="${REFUSAL.operation_uuid}"`) ?? '';
      expect(row).toContain('data-verdict="refused_by_cake"');
      expect(text(row)).toContain('Cake refused the successor, slot unchanged 1 -> 1');
      expect(row).not.toContain(BAD);
      expect(text(markup)).not.toContain('The transaction aborted');
      const swap = element(markup, `="${SWAP.operation_uuid}"`) ?? '';
      expect(swap).toContain('data-verdict="activated"');
      expect(text(swap)).toContain('Activated, slot 1 -> 2');
    });
  }
});

describe("the activation's detail is the runtime's, in its voice, wherever Diagnostics prints it (verdict 2026-09-27 item 4)", () => {
  // The record's `detail` is the runtime's reading of the resident's answer (`cake_activation.py` REJECTION_DETAIL); the
  // resident's own answer is only the phase ABORTED. History, Evidence and the Cake tab print it under one label.
  for (const id of ['history', 'evidence', 'cake'] as const) {
    it(`${id}: prints the refusal's detail under "The runtime's detail", never as the resident's answer`, () => {
      const markup = drawer(withMission(), id);
      const row = element(markup, `="${REFUSAL.operation_uuid}"`) ?? '';
      const words = text(row);
      expect(words).toContain("The runtime's detail");
      expect(words).toContain(REFUSAL.detail ?? '');
      expect(text(markup)).not.toContain("The resident's answer");
    });
  }
});

describe('Diagnostics says a run in the spec 5.1 words and the one duration format (browser r2 N2, verifier R-4)', () => {
  // Run m6ed2dd9 of the 8771 record, stopped by the controller: the page watched it (the fold holds its records).
  const events = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event);
  const watched = { ...foldEvents(initialState(), events), data_mode: 'live' } as ViewState;

  it("History's run rows give the duration as the card and the timeline give it, never as a clock", () => {
    const words = text(drawer(withMission(), 'history'));
    expect(words).toContain('took 22 s');
    expect(words).toContain('took 2 min 54 s');
    expect(words).not.toMatch(/took \d{2}:\d{2}/);
  });

  it("History's run row whose events are not read gives no word and keeps the list's outcome token only in a title", () => {
    const markup = drawer(withMission({ missionRunEvents: {} }), 'history');
    const row = element(markup, `data-run-row="${RUN_2}"`) ?? '';
    expect(text(row)).toContain("word not read yet: the run's own records have not been read");
    expect(text(row)).not.toMatch(/\bstopped\b/);
    expect(row).toMatch(/title="the finished-run list gives the outcome stopped"/);
  });

  it("History's last run line gives the run's word, with the record's token only in a title", () => {
    const markup = drawer(storeShowing(watched, { mission: MISSION, missionError: null, history: HISTORY, missionCandidates: [] }), 'history');
    const line = element(markup, 'data-last-run=') ?? '';
    expect(text(line)).toContain('Stopped by the controller');
    expect(text(line)).not.toMatch(/\bthe outcome stopped\b/);
    expect(line).toMatch(/title="[^"]*stopped[^"]*"/);
  });

  it("Evidence's reason headline gives the run's word, with the record's token only in a title", () => {
    const markup = drawer(storeShowing(watched), 'evidence');
    const words = text(markup);
    expect(words).not.toContain('The run ended stopped');
    expect(words).toContain('The run ended: Stopped by the controller');
    expect(markup).toMatch(/title="[^"]*run\.finished outcome stopped[^"]*"/);
  });
});

describe("the Cake tab's activation times, as the card and the timeline print them (spec 5.4, browser r2 N6)", () => {
  it('prints a time of the mission day without its date, with its ISO time in data-t', () => {
    const markup = drawer(withMission(), 'cake');
    const row = element(markup, `data-activation-detail="${REFUSAL.operation_uuid}"`) ?? '';
    expect(text(row)).toContain('14:20:21Z');
    expect(text(row)).not.toContain('2026-09-27 14:20:21Z');
    expect(row).toContain('data-t="2026-09-27T14:20:21.636Z"');
  });
});

describe("the mission's finish time in History, as the mission card gives it (spec 5.4, 6.7, audit T5)", () => {
  it('prints the start and the derived end in UTC, WITH THEIR DATES (section 12), their ISO times and the end label, and the duration between them', () => {
    const markup = drawer(withMission(), 'history');
    const started = element(markup, 'data-started=') ?? '';
    expect(started).toContain('data-t="2026-09-27T14:16:38.847Z"');
    expect(text(started)).toContain('Started 2026-09-27 14:16:38Z');
    const ended = element(markup, 'data-ended=') ?? '';
    expect(ended).toContain('data-t="2026-09-27T14:24:40.114Z"');
    expect(ended).toMatch(/title="the time the mission(&#x27;|')s last run finished; the mission record carries no end time"/);
    expect(text(ended)).toContain('Ended 2026-09-27 14:24:40Z');
    // 14:16:38.847Z to 14:24:40.114Z.
    expect(text(markup)).toContain('8 min 1 s');
    expect(text(markup)).not.toContain('carries no finish time');
    expect(text(markup)).not.toContain('duration not recorded');
  });

  it('says the end is not recorded only when the finished-run list does not carry the last run, and not read when the list was not read', () => {
    const partial = { ok: true, entries: [(HISTORY as { entries: unknown[] }).entries[0]] } as unknown as StoreSnapshot['history'];
    const missing = drawer(withMission({ history: partial }), 'history');
    expect(element(missing, 'data-ended=')).toBeNull();
    expect(text(missing)).toContain('end time not recorded');
    const unread = drawer(withMission({ history: null }), 'history');
    expect(element(unread, 'data-ended=')).toBeNull();
    expect(text(unread)).toContain('end time not read');
    expect(text(unread)).not.toContain('end time not recorded');
  });
});

function ev(runId: string, seq: number, kind: string, t: string, payload: Record<string, unknown>): ContractEvent {
  return {
    schema_version: '1', seq, run_id: runId, action_id: null, observation_id: null,
    producer: { name: 'demo-runtime', version: '0.1.0', artifact_digest: null, contract_version: '1' },
    source_session: 'rt', t_mono_ns: seq, clock_domain: 'laptop_monotonic', t_wall_utc: t, reason_code: null, provenance: null, kind, payload,
  } as unknown as ContractEvent;
}

// The two runs' own records, as the store keeps them once each finished run is read (`missionRunEvents`).
const RUN_EVENTS: Record<string, ContractEvent[]> = {
  [RUN_1]: [
    ev(RUN_1, 3600, 'run.started', '2026-09-27T14:16:40.100Z', { command_type: 'start_cycle' }),
    ev(RUN_1, 3622, 'run.finished', '2026-09-27T14:17:02.300Z', { outcome: 'failed', exit_status: 5, reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' }),
  ],
  [RUN_2]: [
    ev(RUN_2, 3626, 'run.started', '2026-09-27T14:21:46.510Z', { command_type: 'start_cycle' }),
    ev(RUN_2, 3851, 'state.transition', '2026-09-27T14:24:26.235Z', { machine: 'cycle', from_state: 'STRADDLING', to_state: 'GRASP_CLOSE' }),
    ev(RUN_2, 3906, 'run.finished', '2026-09-27T14:24:40.112Z', { outcome: 'stopped', exit_status: 4, reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE' }),
  ],
};

describe("the mission's and the runs' words in History, as the mission card and the timeline say them (spec 5.1, audit C3)", () => {
  it('says "Mission failed" in the bad colour, never "<mission id> is failed"', () => {
    const markup = drawer(withMission({ missionRunEvents: RUN_EVENTS }), 'history');
    const word = element(markup, 'data-mission-verdict=') ?? '';
    expect(word).toContain('data-mission-verdict="failed"');
    expect(text(word).trim()).toBe('Mission failed');
    expect(word).toContain(BAD);
    expect(text(markup)).not.toContain('is failed');
  });

  it("gives each run the word the mission card and the timeline give it, from the run's own events", () => {
    const markup = drawer(withMission({ missionRunEvents: RUN_EVENTS }), 'history');
    const second = element(markup, `data-run-row="${RUN_2}"`) ?? '';
    expect(second).toContain('data-run-verdict="stopped_by_controller"');
    expect(text(second)).toContain('Stopped by the controller');
    expect(second).toContain(BAD);
    const first = element(markup, `data-run-row="${RUN_1}"`) ?? '';
    expect(first).toContain('data-run-verdict="needs_capability"');
    expect(text(first)).toContain('Needs capability');
    for (const old of ['recorded failed', 'recorded stopped', 'Live stopped', 'Live failed']) expect(text(markup)).not.toContain(old);
    // The mission card and the timeline read the same records to the same words.
    const entries = (HISTORY as { entries: HistoryEntry[] }).entries;
    const card = outcomeCard({ mission: MISSION, missionError: null, history: entries, runEvents: RUN_EVENTS, activeRunId: null, acknowledgements: [], capability: null, drawnRunEvents: {} });
    expect(card.run?.verdict?.id).toBe('stopped_by_controller');
    const timeline = composeTimeline({ mission: MISSION, history: entries, runEvents: RUN_EVENTS, presses: [], links: [], candidates: [] });
    expect(timeline.find((line) => line.key === `${RUN_1}:3622`)?.verdict?.id).toBe('needs_capability');
  });

  it('says Stopped on request for a run whose own events hold a stop.requested', () => {
    const asked = { ...RUN_EVENTS, [RUN_2]: [...(RUN_EVENTS[RUN_2] ?? []).slice(0, 2), ev(RUN_2, 3900, 'stop.requested', '2026-09-27T14:24:39.000Z', { origin: 'operator' }), ...(RUN_EVENTS[RUN_2] ?? []).slice(2)] };
    const second = element(drawer(withMission({ missionRunEvents: asked }), 'history'), `data-run-row="${RUN_2}"`) ?? '';
    expect(second).toContain('data-run-verdict="stopped_on_request"');
    expect(text(second)).toContain('Stopped on request');
  });

  it("says a run's word is not read yet while only the finished-run list carries it, and guesses none (V13)", () => {
    const second = element(drawer(withMission(), 'history'), `data-run-row="${RUN_2}"`) ?? '';
    expect(second).toContain('data-run-verdict="none"');
    expect(text(second)).toContain('not read yet');
    expect(text(second)).not.toContain('Stopped by the controller');
    expect(text(second)).not.toContain('Stopped on request');
  });
});

describe("the mission's runs in History, newest first (spec 6.6)", () => {
  it("lists each target's runs newest first, and says so in the heading", () => {
    const markup = drawer(withMission({ missionRunEvents: RUN_EVENTS }), 'history');
    const second = markup.indexOf(`data-run-row="${RUN_2}"`);
    const first = markup.indexOf(`data-run-row="${RUN_1}"`);
    expect(second).toBeGreaterThanOrEqual(0);
    expect(first).toBeGreaterThan(second);
    expect(text(markup)).toContain('Targets, each with its own runs, newest first');
  });
});

describe('the record this tab holds, in UTC (spec 5.4, browser D4)', () => {
  it('prints each record time as HH:MM:SSZ with its ISO time in data-t and title, never a local time', () => {
    const view = { ...LIVE, recent_events: RUN_EVENTS[RUN_2] ?? [] } as ViewState;
    const markup = drawer(storeShowing(view, { mission: MISSION, missionError: null, history: HISTORY, missionCandidates: [] }), 'history');
    const line = element(markup, 'data-t="2026-09-27T14:24:40.112Z"') ?? '';
    expect(line).toContain('title="2026-09-27T14:24:40.112Z"');
    expect(text(line).trim()).toBe('14:24:40Z');
  });
});

describe("the Cake tab's signature rows carry the main card's earlier-session flag (spec 5.6, browser D12)", () => {
  const check = {
    check_uuid: 'c1', runtime_session: 'rt-20260926T184836Z-e34dcd31', t_wall_utc: '2026-09-26T20:26:13.500Z', policy: 'p', trusted_keys: [], throwaway_public_key: null,
    capsules: 1, unsigned_twins: 0, rows: [], rows_as_expected: 3, rows_not_as_expected: 0, rows_skipped: 0, rows_error: 0, as_expected: true, complete: true, duration_ms: 10,
  };
  const signatureCheck = { signature_check_version: '1', in_flight: false, off_reason: null, policy: 'p', checks: [check], checks_dropped: 0 } as unknown as StoreSnapshot['signatureCheck'];

  it('marks the rows of a check from another runtime session stale, muted, with the sentence the main card says', () => {
    const view = { ...LIVE, runtime_session: 'rt-20260927T140000Z-aaaaaaaa' } as ViewState;
    const markup = drawer(storeShowing(view, { signatureCheck }), 'cake');
    const block = element(markup, 'data-signature-check=""') ?? '';
    expect(block).toContain('data-signature-stale="true"');
    expect(text(block)).toContain('Earlier session: 2026-09-26 20:26:13Z (runtime session rt-20260926T184836Z-e34dcd31). It says nothing about this session.');
    expect(block).toContain('#8b8b96');
  });

  it("draws an earlier session's verdict line and its rule mute, never in the verdict's own colour (verifier r2 defect 8)", () => {
    // A complete, as-expected check with one row skipped would read amber in this session; an earlier session's is mute.
    const partial = { ...signatureCheck, checks: [{ ...check, rows_skipped: 1, complete: false }] } as unknown as StoreSnapshot['signatureCheck'];
    const view = { ...LIVE, runtime_session: 'rt-20260927T140000Z-aaaaaaaa' } as ViewState;
    const run = element(drawer(storeShowing(view, { signatureCheck: partial }), 'cake'), 'data-signature-run=') ?? '';
    expect(run.length).toBeGreaterThan(0);
    expect(run).not.toContain('#fbbf24');
    expect(run).not.toContain('#34d399');
    expect(run).not.toContain('#f87171');
    expect(run).toContain('color:#8b8b96');
  });

  it('does not mark a check of this runtime session', () => {
    const view = { ...LIVE, runtime_session: 'rt-20260926T184836Z-e34dcd31' } as ViewState;
    const block = element(drawer(storeShowing(view, { signatureCheck }), 'cake'), 'data-signature-check=""') ?? '';
    expect(block).toContain('data-signature-stale="false"');
    expect(text(block)).not.toContain('Earlier session');
  });
});

describe("Evidence's decision frame says what the record says about it (spec 5.1, 5.2, browser D5)", () => {
  // Run m6ed2dd9's shape: the straddle_far_edge_row predicate consumed the newest frame, and the stop
  // (base_not_zero_before_segment) names no frame in its own records.
  const events = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event);
  const folded = foldEvents(initialState(), events);
  const shown = folded.decision_frame?.primary_observation_id ?? '';

  function naming(ids: string[]): ViewState {
    const renamed = events.map((event) => {
      if (event.kind !== 'run.finished') return event;
      const payload = event.payload as unknown as Record<string, unknown>;
      const failure = payload['failure'] as Record<string, unknown>;
      return { ...event, payload: { ...payload, failure: { ...failure, observation_ids: ids } } } as unknown as ContractEvent;
    });
    return { ...foldEvents(initialState(), renamed), data_mode: 'live' };
  }

  function frameSection(view: ViewState, mode: ScreenMode = 'live'): string {
    return element(drawer(storeShowing(view), 'evidence', mode), 'data-evidence-frame=') ?? '';
  }

  it('names the predicate that consumed the frame and says the stop names no frame, never that the result was computed from it', () => {
    expect(shown).not.toBe('');
    const section = frameSection({ ...folded, data_mode: 'live' });
    const words = text(section);
    expect(words).toContain('the newest frame a predicate or an action consumed: the predicate straddle_far_edge_row');
    expect(section).toContain('data-frame-tie="none"');
    expect(words).toContain("The stop's records name no frame, so this frame is not tied to the stop.");
    expect(words).not.toContain('Held for this result');
    expect(words).not.toContain('this result was computed from');
    expect(words).not.toContain('The frame is tied to the stop');
  });

  it("lists under the evidence the controller named only what the stop named (verdict 2026-09-27 item 9)", () => {
    // The record's failure.observation_ids is []: the heading says the stop named none, and the straddle frame the false
    // predicate read is listed apart, as not named by the stop.
    const markup = drawer(storeShowing({ ...folded, data_mode: 'live' }), 'evidence');
    const named = element(markup, 'data-evidence-named=') ?? '';
    expect(named.length).toBeGreaterThan(0);
    expect(text(named)).toContain('The stop named no observation');
    expect(named).not.toContain(shown);
    const read = element(markup, 'data-evidence-read=') ?? '';
    expect(text(read)).toContain('not named by the stop');
    expect(read).toContain(shown);
    // A stop that names the frame lists it under the named heading and not again under the predicates'.
    const naming2 = drawer(storeShowing(naming([shown])), 'evidence');
    expect(element(naming2, 'data-evidence-named=') ?? '').toContain(shown);
    expect(element(naming2, 'data-evidence-read=') ?? '').not.toContain(shown);
  });

  it("says the frame is tied to the stop only when the stop's own records name it", () => {
    const section = frameSection(naming(['obs-before', shown]));
    expect(section).toContain('data-frame-tie="tied"');
    expect(text(section)).toContain("The stop's records name this frame, so it is tied to the stop.");
    expect(text(section)).not.toContain('name no frame');
  });

  it('names the other frame when the stop names another one', () => {
    const section = frameSection(naming(['obs-elsewhere']));
    expect(section).toContain('data-frame-tie="other"');
    expect(text(section)).toContain("The stop's records name another frame, obs-elsewhere, so this frame is not tied to the stop.");
  });

  it('marks a rehearsal frame Rehearsal and a recording Recording, in sentence case, with no age in records', () => {
    const rehearsal = frameSection({ ...folded, data_mode: 'synthetic' }, 'synthetic');
    expect(element(rehearsal, 'frame__watermark') ?? '').toMatch(/>Rehearsal</);
    expect(text(rehearsal)).toContain('Generated by the rehearsal, not a camera image.');
    const recording = frameSection({ ...folded, data_mode: 'live' }, 'replay');
    expect(element(recording, 'frame__watermark') ?? '').toMatch(/>Recording</);
    for (const section of [rehearsal, recording]) {
      // Recorded ids keep their own spelling (spec 5.3), so only the mode's own words are looked for.
      expect(section).not.toMatch(/SYNTHETIC|GENERATED|>synthetic<|>replay<|synthetic source|synthetic perception/);
      expect(text(section)).not.toMatch(/\bago\b/);
      expect(text(section)).toMatch(/pinned at seq \d+/);
    }
  });
});

describe('Diagnostics counts no age and transforms no label (spec 5.1, 5.2, 5.4, 6.8, browser D9, verifier V4)', () => {
  // The 8771 record at 14:25:00Z, with the robot, a slot check, a signature check, a recorded pose, a tap and a stream
  // line: every place the drawer used to count an age against this display's clock has something to count.
  const T0 = Date.parse('2026-09-27T14:25:00.000Z');
  const ALL: DrawerId[] = ['history', 'logs', 'evidence', 'connections', 'cake'];
  const run = buildStoppedHoldingRun(2, 100, 1_000_000_000).steps.map((step) => step.event);
  const check = parseCapabilityCheckState(fixtureCheckAnswer());
  const signatureCheck = {
    signature_check_version: '1', in_flight: false, off_reason: null, policy: 'p', checks_dropped: 0,
    checks: [{
      check_uuid: 'c1', runtime_session: 'rt-20260927T140000Z-aaaaaaaa', t_wall_utc: '2026-09-27T14:20:13.500Z', policy: 'p', trusted_keys: [], throwaway_public_key: null,
      capsules: 1, unsigned_twins: 0, rows: [], rows_as_expected: 3, rows_not_as_expected: 0, rows_skipped: 0, rows_error: 0, as_expected: true, complete: true, duration_ms: 10,
    }],
  } as unknown as StoreSnapshot['signatureCheck'];

  afterEach(() => {
    vi.useRealTimers();
  });

  function diagnostics(stale: boolean): ConsoleStore {
    const folded = foldEvents(initialState(), run);
    const view = { ...folded, data_mode: 'live', runtime_session: 'rt-20260927T140000Z-aaaaaaaa', snapshot_at_ms: T0 - 30_000 } as ViewState;
    const store = storeShowing(view, {
      mission: MISSION, missionError: stale ? 'the runtime did not answer' : null, missionReadAtMs: T0 - 45_000, history: HISTORY, missionCandidates: [], missionRunEvents: RUN_EVENTS,
      robot: robotStatus(), robotReadAtMs: stale ? T0 - 40_000 : T0 - 1_000, robotError: stale ? 'the robot did not answer' : null,
      capabilityCheck: check, capabilityCheckReadAtMs: T0 - 50_000, signatureCheck, signatureCheckReadAtMs: T0 - 50_000,
    });
    const held = store.getSnapshot();
    const telemetry = {
      ...held.telemetry,
      tap: { state: 'connected', reason: null, available: true, receive_age_ms: 120, measured_rate_hz: 10 },
      tap_received_at_ms: stale ? T0 - 40_000 : T0 - 1_000,
    } as unknown as StoreSnapshot['telemetry'];
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, telemetry };
    return store;
  }

  function at(ms: number, store: ConsoleStore, id: DrawerId): string {
    vi.useFakeTimers();
    vi.setSystemTime(ms);
    return drawer(store, id);
  }

  for (const stale of [false, true]) {
    for (const id of ALL) {
      it(`${id}${stale ? ', with the robot and the mission not read since' : ''}: no "ago", no age in words, no transformed label, no all-caps word`, () => {
        const markup = at(T0, diagnostics(stale), id);
        const words = text(markup);
        expect(words).not.toMatch(/\bago\b/);
        expect(words).not.toMatch(/\b\d+(\.\d+)? ?(ms|s|min|m|h) old\b/);
        expect(markup).not.toMatch(/text-transform:\s*uppercase/);
        for (const loud of ['SELECTED', 'SYNTHETIC', 'GENERATED', 'STALE', 'SIMULATED']) expect(words).not.toMatch(new RegExp(`(?<![A-Z_])${loud}(?![A-Z_])`));
        // SENTENCE CASE (verifier r2 R-5): no sentence in capitals, and no word shouted for emphasis in the reference's
        // prose. Recorded tokens (slot states, controller states) keep their own spelling and are not prose.
        expect(words).not.toMatch(/\b[A-Z]{2,}(?: [A-Z]{2,}){2,}\./);
        for (const shout of ['ARE', 'NOT', 'BEFORE', 'AFTER', 'CONTROLLER']) expect(words).not.toMatch(new RegExp(`(?<![A-Z_])${shout}(?![A-Z_])`));
      });
    }
  }

  it('says the runtime data mode in Connections as the spec 5.2 word, with the raw value only in a title', () => {
    for (const [raw, word] of [
      ['live', 'Live'],
      ['synthetic', 'Rehearsal'],
    ] as const) {
      const store = diagnostics(false);
      const held = store.getSnapshot();
      (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, view: { ...held.view, data_mode: raw } };
      const markup = at(T0, store, 'connections');
      const words = text(markup);
      expect(words).toContain(`Runtime data mode ${word}`);
      expect(words).not.toContain(`Runtime data mode ${raw}`);
      expect(markup).toMatch(new RegExp(`title="${raw}"[^>]*>${word}<`));
    }
  });

  it('says the retained readings in Connections with the fixed time they were last read, in UTC', () => {
    const markup = at(T0, diagnostics(true), 'connections');
    const words = text(markup);
    expect(words).toContain('the last snapshot reached this page at 14:24:30Z');
    expect(text(element(markup, 'data-t="2026-09-27T14:24:30.000Z"') ?? '')).toContain('the last snapshot reached this page at 14:24:30Z');
    expect(words).toContain('Not read since 14:24:20Z');
    expect(text(element(markup, 'data-t="2026-09-27T14:24:20.000Z"') ?? '')).toContain('Not read since 14:24:20Z');
  });

  it('renders Connections byte-identical 10 s later while samples, stream lines and snapshots keep arriving at a quiet end (browser N4, verifier R-2)', () => {
    // A quiet end state on a live runtime: the tap samples every 100 ms, stream lines keep arriving, the snapshot is polled
    // every 5 s and the robot is re-read. Nothing about the run or the robot changed, so the tab says each arrival as a
    // rate and the fixed time it began, never the time of the newest sample.
    const store = diagnostics(false);
    const held = store.getSnapshot();
    const since = T0 - 60_000;
    const arriving = (ms: number): void => {
      (store as unknown as { snapshot: StoreSnapshot }).snapshot = {
        ...held,
        view: { ...held.view, snapshot_at_ms: ms - 2_000 },
        telemetry: { ...held.telemetry, tap_received_at_ms: ms - 500, preview_received_at_ms: ms - 400, tap_since_ms: since, line_since_ms: since },
        snapshotSinceMs: since,
        robotReadAtMs: ms - 1_000,
      } as unknown as StoreSnapshot;
    };
    arriving(T0);
    const first = at(T0, store, 'connections');
    arriving(T0 + 10_000);
    const later = at(T0 + 10_000, store, 'connections');
    expect(later).toBe(first);
    const words = text(first);
    expect(words).toContain('Continuous telemetry is arriving at 10.0 Hz, as the runtime measures it, since 14:24:00Z');
    expect(words).toContain('transport activity: stream lines arriving since 14:24:00Z');
    expect(words).toContain('answering since 14:24:00Z, read every 5 s');
  });

  it('renders Connections byte-identical 20 s later when nothing new was read (spec 6.8, INV-9)', () => {
    for (const stale of [false, true]) {
      const store = diagnostics(stale);
      const first = at(T0 + 20_000, store, 'connections');
      const later = at(T0 + 40_000, store, 'connections');
      expect(later, stale ? 'stale' : 'fresh').toBe(first);
    }
  });
});

describe('the drawer transforms no label in CSS either (spec section 1, browser D9)', () => {
  // Every class a loaded stylesheet upper-cases is listed in design.css's `.dc` override, which sets it back to none:
  // the drawer renders inside the `.dc` root on both screens.
  function uppercased(css: string): string[] {
    const classes: string[] = [];
    for (const match of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/text-transform:\s*uppercase/.test(match[2] ?? '')) continue;
      for (const selector of (match[1] ?? '').split(',')) classes.push(selector.trim());
    }
    return classes;
  }
  const override = /No label on the demo screen[^*]*\*\/\s*([^{]+)\{([^}]*)\}/.exec(designCss);

  it('lists every upper-cased selector of base.css, console.css, engineering.css and sources.css under .dc with text-transform none', () => {
    expect(override).not.toBeNull();
    const listed = (override?.[1] ?? '').split(',').map((item) => item.trim());
    expect(override?.[2] ?? '').toMatch(/text-transform:\s*none/);
    const found = [baseCss, consoleCss, engineeringCss, sourcesCss].flatMap(uppercased);
    expect(found).toEqual(expect.arrayContaining(['.rail__title', '.frame__watermark', '.frame__caption-lead']));
    for (const selector of found) expect(listed, selector).toContain(`.dc ${selector}`);
  });
});
