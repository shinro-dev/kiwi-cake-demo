// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The engineering tab, actually rendered.
//
// The rules it follows are tested as pure functions in `model/engineering.test.ts`; this file is about what an
// operator would read on the screen, because a figure can be right in a function and still be printed under a badge
// that contradicts it. That is exactly what the timing badge did: it said "0.0 ms measured" over a table whose body
// said nothing had been recorded.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import timingsSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/timings-and-the-evidence-of-the-run-in-progress.json';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import type { HistoryEntry, TapSample } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import { applySnapshot, initialState } from '../model/reducer';
import { ConsoleStore } from '../store/store';
import { EngineeringView } from './EngineeringView';

const NOW = 5_000_000;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

const store = new ConsoleStore(new SyntheticSource());

function snapshotWith(patch: Record<string, unknown>) {
  return { ...store.getSnapshot(), ...patch } as ReturnType<ConsoleStore['getSnapshot']>;
}

function markup(patch: Record<string, unknown>): string {
  return renderToStaticMarkup(createElement(EngineeringView, { snapshot: snapshotWith(patch), store, nowMs: NOW }));
}

function render(patch: Record<string, unknown>): string {
  return text(markup(patch));
}

let seq = 0;

function envelope(extra: Partial<EventEnvelope> = {}): EventEnvelope {
  seq += 1;
  return {
    schema_version: '1',
    seq,
    run_id: 'run-a',
    action_id: null,
    observation_id: null,
    producer: { name: 'controller', version: '0', artifact_digest: null, contract_version: '1' },
    source_session: 'session-a',
    t_mono_ns: seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: null,
    reason_code: null,
    provenance: null,
    ...extra,
  };
}

function event<K extends ContractEvent['kind']>(
  kind: K,
  payload: Extract<ContractEvent, { kind: K }>['payload'],
  extra: Partial<EventEnvelope> = {},
): ContractEvent {
  return { ...envelope(extra), kind, payload } as ContractEvent;
}

function entry(extra: Partial<HistoryEntry>): HistoryEntry {
  return {
    run_id: 'run-x',
    command_id: null,
    command_type: null,
    started_wall_utc: null,
    finished_wall_utc: null,
    outcome: null,
    exit_status: null,
    evidence_complete: null,
    evidence_gaps: null,
    artifact_digest: null,
    adopted: null,
    data_mode: null,
    bundle_digest: null,
    ...extra,
  };
}

describe('the engineering tab is read in groups', () => {
  it('names every group and says in one sentence what each one is for', () => {
    const words = render({});
    for (const subject of [
      'Where the numbers come from',
      'What the controller looked at',
      'Who holds the robot',
      'How long the measured spans took',
      'What the resident is doing in this mission',
      'What the finished runs were',
    ]) {
      expect(words, subject).toContain(subject);
    }
  });
});

describe('the timing panel', () => {
  it('prints no figure at all before a span has been measured', () => {
    const words = render({});
    expect(words).toContain('No span has been measured in this run');
    expect(words).not.toContain('0.0 ms measured');
  });

  it('keeps a span measured in two clocks as two rows, with the count and the window each rests on', () => {
    const words = render({ view: applySnapshot(initialState(), timingsSnapshot as unknown as Snapshot) });
    expect(words).toContain('max observed');
    expect(words).toContain('window (seq)');
    expect(words).toContain('pi_monotonic');
    expect(words).toContain('laptop_monotonic');
    expect(words).toContain('2 clock domain(s)');
    expect(words).toContain('4 to 17');
    // 742 ms and 31 ms are two separate totals. Their sum is a figure this screen must never print.
    expect(words).toContain('742.0 ms');
    expect(words).toContain('31.0 ms');
    expect(words).not.toContain('773.0 ms');
  });

  it('says which aggregate the figures come from and how wide its window is', () => {
    const words = render({ view: applySnapshot(initialState(), timingsSnapshot as unknown as Snapshot) });
    expect(words).toContain('Aggregated by the runtime');
    expect(words).toContain('synthetic-run-0001');
  });
});

describe('the tap and the sample a decision consumed', () => {
  function tap(extra: Partial<TapSample>): TapSample {
    return { source_stamp: null, joints: null, base_velocity: null, receive_age_ms: null, subscriber_gap_count: null, publisher_drop_count: null, ...extra };
  }

  it('shows all three base velocities with their units, including the lateral one', () => {
    const words = render({
      telemetry: {
        tap: tap({ state: 'connected', base_velocity: { x_mps: 0.12, y_mps: -0.04, theta_dps: 3.5 } }),
        preview: null,
        tap_received_at_ms: NOW,
        preview_received_at_ms: null,
      },
    });
    expect(words).toContain('0.120 m/s');
    expect(words).toContain('-0.040 m/s');
    expect(words).toContain('3.50 deg/s');
  });

  it('states a missing component as unavailable and still shows the two that were reported', () => {
    const words = render({
      telemetry: {
        tap: tap({ state: 'connected', base_velocity: { x_mps: 0.2, y_mps: null, theta_dps: 1 } }),
        preview: null,
        tap_received_at_ms: NOW,
        preview_received_at_ms: null,
      },
    });
    expect(words).toContain('y unavailable');
    expect(words).toContain('0.200 m/s');
  });

  it('refuses to compare a tap and a consumed sample from two different source sessions', () => {
    const view = {
      ...initialState(),
      measured_state: {
        joints: { gripper: 70 },
        base_velocity: { x_mps: null, y_mps: null, theta_dps: null },
        consumed_source_stamp: { session: 'host-b', seq: 3, sample_mono_ns: 9, clock_domain: 'pi_monotonic' },
        seq: 4,
      },
    };
    const words = render({
      view,
      telemetry: {
        tap: tap({ state: 'connected', source_stamp: { session: 'host-a', seq: 40, sample_mono_ns: 5, clock_domain: 'pi_monotonic' } }),
        preview: null,
        tap_received_at_ms: NOW,
        preview_received_at_ms: null,
      },
    });
    expect(words).toContain('Not comparable');
    expect(words).toContain('host-a');
    expect(words).toContain('host-b');
  });

  it('states no unit for a joint, because no record states one and the gripper is not in degrees', () => {
    const raw = markup({});
    expect(raw).toContain('<th>representation</th>');
    expect(raw).toContain('<td class="muted">not stated</td>');
    expect(text(raw)).not.toMatch(/gripper[^|]{0,40}\bdeg\b/);
  });
});

describe('the finished runs', () => {
  it('never puts a hardware run in the same group as a synthetic one', () => {
    const words = render({
      history: {
        ok: true,
        entries: [
          entry({ run_id: 'run-live', data_mode: 'live', bundle_digest: 'sha256:1111aaaa2222bbbb' }),
          entry({ run_id: 'run-sim', data_mode: 'synthetic', bundle_digest: 'sha256:1111aaaa2222bbbb' }),
        ],
      },
    });
    expect(words).toContain('recorded from hardware');
    expect(words).toContain('recorded from the synthetic source');
    expect(words).toContain('run-live');
    expect(words).toContain('run-sim');
  });

  it('keeps a run that recorded no mode apart and says it will not be filled in', () => {
    const words = render({ history: { ok: true, entries: [entry({ run_id: 'run-old' })] } });
    expect(words).toContain('mode not recorded');
    expect(words).toContain('never filled in from this runtime');
  });

  it('keeps the three different reasons a run list can be empty apart from each other', () => {
    expect(render({})).toContain('has not been read yet');
    expect(render({ history: { ok: false, reason_code: 'history_unreadable' } })).toContain('history_unreadable');
    expect(render({ history: { ok: true, entries: [] } })).toContain('No run has finished in this runtime session');
  });
});

describe('an open recording', () => {
  const recording = [
    event('run.started', { command_id: 'c1', command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: false }, { t_mono_ns: 0 }),
    event('state.transition', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }, { t_mono_ns: 400_000_000 }),
    event('state.transition', { machine: 'cycle', from_state: 'SEARCH', to_state: 'ALIGN' }, { t_mono_ns: 1_900_000_000 }),
  ];

  it('carries the recording own provenance and its own elapsed time, not this runtime mode', () => {
    const words = render({ replay: { run_id: 'run-recorded', events: recording, cursor: 3, origins: [] } });
    expect(words).toContain('recorded from hardware');
    expect(words).toContain('Recorded time');
    expect(words).toContain('1.9 s');
    expect(words).toContain('run-recorded');
    // The cursor is at the end of the recording, so the transport offers the start again rather than doing nothing.
    expect(words).toContain('play from the start');
    expect(markup({ replay: { run_id: 'run-recorded', events: recording, cursor: 1, origins: [] } })).toContain('aria-label="replay cursor"');
  });

  it('says a recording states no mode rather than reading this runtime as its own', () => {
    const words = render({ replay: { run_id: 'run-old', events: recording.slice(1), cursor: 2, origins: [] } });
    expect(words).toContain('does not state what it ran against');
  });

  it('marks the telemetry beside a recording as live lines that are not part of it', () => {
    const words = render({ replay: { run_id: 'run-recorded', events: recording, cursor: 3, origins: [] } });
    expect(words).toContain('live lines, not part of the open recording');
  });
});

describe('what the run counted', () => {
  it('never labels an observed state re-entry as a retry', () => {
    const view = {
      ...initialState(),
      machine: {
        machine: 'cycle',
        state: 'ALIGN',
        from_state: 'GRASP_CLOSE',
        since_seq: 9,
        history: [
          { state: 'SEARCH', seq: 1 },
          { state: 'ALIGN', seq: 2 },
          { state: 'GRASP_CLOSE', seq: 3 },
          { state: 'ALIGN', seq: 4 },
        ],
      },
    };
    const words = render({ view });
    // ALIGN was entered twice (seq 2 and seq 4), so it was entered AGAIN once. The earlier pin, "ALIGN entered again 2
    // time(s)", counted the first entry as a re-entry (owner's status 2026-09-27 LOW 3; run m6ed2dd9 entered ALIGN at seq
    // 3708 and again at 3747, and the main timeline shows one "ALIGN again").
    expect(words).toContain('ALIGN entered again 1 time(s), 2 entries in all');
    expect(words).not.toContain('ALIGN entered again 2 time(s)');
    expect(words).not.toContain('SEARCH entered again');
    expect(words).toContain('This is an observation, not a retry count');
    expect(words).toContain('a state entered again is not necessarily a retry');
  });

  it('says no run has reported a total yet, rather than printing a zero or the word unavailable', () => {
    const words = render({});
    expect(words).toContain('automatic retries no run yet');
    expect(words).toContain('task control interventions no run yet');
    expect(words).not.toContain('automatic retries 0');
  });

  it('states who counted each figure and on what basis, beside the figure itself', () => {
    const words = render({});
    expect(words).toContain('counted by, and on what basis');
    expect(words).toContain('the controller, from its own per-kind counters, summed into the run record when the run ends');
    expect(words).toContain('this console, from the transitions it folded');
  });

  it('says a total is not reported yet while a run is still going, and where it will come from', () => {
    const view = { ...initialState(), active_run: { run_id: 'run-a', command_id: null, command_type: null, status: 'running', state: 'SEARCH', started_seq: 1 } };
    const words = render({ view });
    expect(words).toContain('automatic retries not yet');
    expect(words).toContain('summed into the run record when the run ends');
  });
});
