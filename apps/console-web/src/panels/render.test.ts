// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The three new panels, actually rendered.
//
// Everything else in this suite tests the pure functions behind them, which is where the rules live; this file exists
// because a panel that throws on its first render says nothing at all, and a screen that shows nothing is the exact
// failure these panels were added to prevent. It renders them to static markup (no DOM needed) and reads the words out
// of the result, so the assertion is about what an operator would actually see.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import failedWithAReason from '@kiwi/demo-contracts/fixtures/event/run.finished.failed-with-a-reason.json';
import type { ContractEvent } from '../contracts/envelope';
import type { PreviewSample, TapSample, UnresolvedCommand } from '../api/types';
import { previewView, tapView } from '../model/sources';
import { applyEvent, applySnapshot, initialState } from '../model/reducer';
import type { ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import { SyntheticSource } from '../api/synthetic';
import { FailurePanel } from './FailurePanel';
import { PreviewPanel, SourcesPanels, TapPanel } from './SourcesPanel';
import { UncertainPanel } from './UncertainPanel';

const NOW = 5_000_000;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

function storeSnapshot(patch: Record<string, unknown>) {
  const store = new ConsoleStore(new SyntheticSource());
  return { ...store.getSnapshot(), ...patch } as ReturnType<ConsoleStore['getSnapshot']>;
}

function stateWith(event: ContractEvent): ViewState {
  return applyEvent(applySnapshot(initialState(), runningSnapshot as unknown as Snapshot), event);
}

describe('the failure panel renders', () => {
  it('shows the phase, the code and the controller sentence for a real recorded refusal', () => {
    const markup = renderToStaticMarkup(createElement(FailurePanel, { view: stateWith(failedWithAReason as unknown as ContractEvent) }));
    const words = text(markup);
    expect(words).toContain('search_placement_infeasible');
    expect(words).toContain('SEARCH');
    expect(words).toContain('nothing was picked up');
    expect(words).toContain('anchor_margin_mm');
    expect(words).toContain('synthetic-obs-0001');
  });

  it('shows an unknown code as the code itself, with its payload, and never as a blank', () => {
    const unknown = {
      ...(failedWithAReason as unknown as ContractEvent),
      payload: {
        ...(failedWithAReason as unknown as { payload: Record<string, unknown> }).payload,
        reason_code: 'a_code_from_a_newer_controller',
        phase: 'SOME_NEW_STATE',
      },
    } as ContractEvent;
    const words = text(renderToStaticMarkup(createElement(FailurePanel, { view: stateWith(unknown) })));
    expect(words).toContain('a_code_from_a_newer_controller');
    expect(words).toContain('SOME_NEW_STATE');
    expect(words).toContain('has no sentence for');
  });

  it('renders nothing at all when there is nothing to explain', () => {
    expect(renderToStaticMarkup(createElement(FailurePanel, { view: initialState() }))).toBe('');
  });
});

describe('the source panels render', () => {
  function tap(extra: Partial<TapSample>): TapSample {
    return { source_stamp: null, joints: null, base_velocity: null, receive_age_ms: null, subscriber_gap_count: null, publisher_drop_count: null, ...extra };
  }

  function preview(extra: Partial<PreviewSample>): PreviewSample {
    return { role: 'wrist', seq: null, capture_clock: null, receive_age_ms: null, measured_fps: null, available: false, reason: null, ...extra };
  }

  it('says not attached, with the server reason, and shows no numbers', () => {
    const words = text(renderToStaticMarkup(createElement(TapPanel, { view: tapView(tap({ state: 'disconnected', reason: 'tap_not_configured' }), null, NOW) })));
    expect(words).toContain('not attached');
    expect(words).toContain('tap_not_configured');
    expect(words).toContain('unavailable');
    expect(words).not.toMatch(/\b0 Hz\b/);
  });

  it('says stalled and keeps the last stamp while showing no values', () => {
    const view = tapView(
      tap({
        state: 'stalled',
        reason: 'no_sample_within_the_fresh_window',
        source_stamp: { session: 'host-session-a', seq: 991, sample_mono_ns: 42, clock_domain: 'pi_monotonic' },
        receive_age_ms: 7400,
      }),
      NOW,
      NOW,
    );
    const words = text(renderToStaticMarkup(createElement(TapPanel, { view })));
    expect(words).toContain('stalled');
    expect(words).toContain('991');
    expect(words).toContain('none are being reported');
  });

  it('names both clock domains on a connected tap and never merges them', () => {
    const view = tapView(
      tap({
        state: 'connected',
        source_stamp: { session: 'host-session-a', seq: 7, sample_mono_ns: 5, clock_domain: 'pi_monotonic' },
        clock_domains: { sample: 'pi_monotonic', receive: 'laptop_monotonic' },
        receive_age_ms: 31,
        measured_rate_hz: 9.8,
        joints: { shoulder_pan: 0.1 },
      }),
      NOW,
      NOW,
    );
    const words = text(renderToStaticMarkup(createElement(TapPanel, { view })));
    expect(words).toContain('pi_monotonic');
    expect(words).toContain('laptop_monotonic');
    expect(words).toContain('9.8 Hz');
    expect(words).toContain('neither is ever subtracted from the other');
  });

  it('prints the capture age as unavailable with the reason it cannot be computed', () => {
    const view = previewView(
      preview({
        state: 'connected',
        available: true,
        measured_fps: 8.5,
        service_latency_ms: 12.5,
        capture_age_reason: 'no_clock_relation_between_source_and_laptop',
        capture_clock: { session: 'camera-session-a', seq: 40, sample_mono_ns: 99, clock_domain: 'pi_monotonic' },
      }),
      NOW,
      NOW,
    );
    const words = text(renderToStaticMarkup(createElement(PreviewPanel, { view })));
    expect(words).toContain('no_clock_relation_between_source_and_laptop');
    expect(words).toContain('12.5 ms');
    expect(words).toContain('8.5 fps');
    expect(words).toContain('wrist');
  });

  it('draws the three absences with three different words', () => {
    const words = (sample: PreviewSample): string =>
      text(renderToStaticMarkup(createElement(PreviewPanel, { view: previewView(sample, NOW, NOW) })));
    expect(words(preview({ state: 'disconnected', reason: 'preview_not_configured' }))).toContain('not attached');
    expect(words(preview({ state: 'disconnected', reason: 'open_failed' }))).toContain('disconnected');
    expect(words(preview({ state: 'stalled', reason: 'no_frame_within_the_fresh_window' }))).toContain('stalled');
  });

  // The compact summary the presentation screen carries. The full identity of each source is in the engineering tab;
  // what this has to get right is that the two rows are there, that the runtime's reason is on each of them, and that
  // a source nobody configured does not read as a source that is missing something.
  it('summarises both sources in two rows, each with the runtime reason', () => {
    const snapshot = storeSnapshot({
      telemetry: {
        tap: tap({ state: 'connected', source_stamp: { session: 'host-session-a', seq: 77, sample_mono_ns: 5, clock_domain: 'pi_monotonic' } }),
        preview: preview({ state: 'disconnected', reason: 'preview_not_configured' }),
        tap_received_at_ms: NOW - 400,
        preview_received_at_ms: null,
      },
    });
    const words = text(renderToStaticMarkup(createElement(SourcesPanels, { snapshot, nowMs: NOW })));
    expect(words).toContain('host telemetry tap');
    expect(words).toContain('camera preview');
    expect(words).toContain('host-session-a');
    expect(words).toContain('77');
    expect(words).toContain('preview_not_configured');
    expect(words).toContain('nothing is configured to attach to');
    expect(words).toContain('400 ms ago');
  });

  it('says no line has reached this tab yet, rather than calling an age unavailable', () => {
    const snapshot = storeSnapshot({ telemetry: { tap: null, preview: null, tap_received_at_ms: null, preview_received_at_ms: null } });
    const words = text(renderToStaticMarkup(createElement(SourcesPanels, { snapshot, nowMs: NOW })));
    expect(words).toContain('no line yet');
    expect(words).toContain('no sample has arrived');
    expect(words).toContain('no frame has arrived');
    expect(words).not.toContain('unavailable');
  });
});

describe('the uncertain panel renders', () => {
  const store = new ConsoleStore(new SyntheticSource());
  const block: UnresolvedCommand = {
    command_id: 'abcd1234ef0123456789',
    run_id: 'sc-20260101T000000Z-abcd1234',
    command_type: 'start_cycle',
    reason_code: 'runtime_restarted_before_child_recorded',
    classification: 'ran_and_was_interrupted',
    block_reason_code: 'owner_unresolved',
    question: 'confirm that none is running and say how it ended, after looking at the robot',
    findings: ['no_controller_running', 'controller_finished', 'controller_stopped_by_operator'],
    evidence: {
      process: { alive: false, readable: true, found_by: null },
      run_files: { readable: true, terminal_record: false, event_records: 41, output_present: true, outcome: null },
      observations: { checked: true, readable: true, files_after_accept: 3 },
      journal: { status: 'uncertain', reason_code: 'runtime_restarted_before_child_recorded', child_identity: true },
      unreadable: [],
    },
    attestations: [],
    blocked_commands: ['start_cycle', 'observe'],
  };

  it('states outright that whether it ran is not known, and offers the attestation wording', () => {
    const words = text(renderToStaticMarkup(createElement(UncertainPanel, { snapshot: storeSnapshot({ liveUnresolved: block }), store })));
    expect(words).toContain('NOT known');
    expect(words).toContain('owner_unresolved');
    expect(words).toContain('I am stating this; the console did not verify it');
    expect(words).toContain('This sends a statement. It does not send the command.');
  });

  it('shows the machine evidence the daemon read, check by check', () => {
    const words = text(renderToStaticMarkup(createElement(UncertainPanel, { snapshot: storeSnapshot({ liveUnresolved: block }), store })));
    expect(words).toContain('process table');
    expect(words).toContain("the run's own files");
    expect(words).toContain('the observation store');
    expect(words).toContain('41 record(s) written');
  });

  it('offers only the findings the runtime named, each with its own sentence', () => {
    const words = text(renderToStaticMarkup(createElement(UncertainPanel, { snapshot: storeSnapshot({ liveUnresolved: block }), store })));
    for (const finding of block.findings) expect(words, finding).toContain(finding);
    expect(words).toContain('I looked at the robot and no controller is running.');
  });

  it('keeps the controls off and says so, when the block cannot be described at all', () => {
    const opaque = { ...block, command_id: '', findings: [], evidence: null, question: null };
    const words = text(renderToStaticMarkup(createElement(UncertainPanel, { snapshot: storeSnapshot({ liveUnresolved: opaque }), store })));
    expect(words).toContain('could not read the detail');
    expect(words).toContain('The controls stay off');
  });

  it('renders nothing when the runtime reports no block', () => {
    expect(renderToStaticMarkup(createElement(UncertainPanel, { snapshot: storeSnapshot({ liveUnresolved: null }), store }))).toBe('');
  });
});
