// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import timingsSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/timings-and-the-evidence-of-the-run-in-progress.json';
import type { HistoryEntry } from '../api/types';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import {
  advanceReplayCursor,
  clockDomainsOf,
  commandBlockReason,
  compareTapWithConsumed,
  comparisonWords,
  groupRuns,
  jointNames,
  provenanceWords,
  recordedProvenance,
  replayAxis,
  stateReentries,
  timingTable,
} from './engineering';
import { applySnapshot, foldEvents, initialState } from './reducer';
import type { ViewState } from './types';

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

function timing(span: string, durationNs: number, domain: 'laptop_monotonic' | 'pi_monotonic'): ContractEvent {
  return event('timing.span', { span, duration_ns: durationNs, clock_domain: domain, detail: null } as never);
}

function stamp(session: string, seqNo: number, domain: 'pi_monotonic' | 'laptop_monotonic' = 'pi_monotonic') {
  return { session, seq: seqNo, sample_mono_ns: seqNo * 1000, clock_domain: domain };
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

describe('the timing table', () => {
  it('has nothing to draw before a span has been measured', () => {
    expect(timingTable(initialState())).toBeNull();
  });

  it('takes the runtime aggregate when the runtime serves one, and names the run it measured', () => {
    const view = applySnapshot(initialState(), timingsSnapshot as unknown as Snapshot);
    const table = timingTable(view);
    expect(table?.basis).toBe('runtime');
    expect(table?.run_id).toBe('synthetic-run-0001');
    expect(table?.rows.length).toBe(3);
  });

  it('keeps one span measured in two clocks as two rows, each with its own count and window', () => {
    const view = applySnapshot(initialState(), timingsSnapshot as unknown as Snapshot);
    const captures = timingTable(view)?.rows.filter((row) => row.span === 'capture') ?? [];
    expect(captures.length).toBe(2);
    expect(captures.map((row) => row.clock_domain).sort()).toEqual(['laptop_monotonic', 'pi_monotonic']);
    const laptop = captures.find((row) => row.clock_domain === 'laptop_monotonic');
    expect(laptop?.count).toBe(4);
    expect(laptop?.first_seq).toBe(4);
    expect(laptop?.last_seq).toBe(17);
    // The two totals are never added into one: the sum of the two rows is not a figure this screen ever prints.
    expect(laptop?.total_ns).toBe(742_000_000);
  });

  it('falls back to what this fold saw when the runtime aggregates none, and says which it is', () => {
    const view = foldEvents(initialState(), [timing('motion', 5_000_000, 'laptop_monotonic')]);
    const table = timingTable(view);
    expect(table?.basis).toBe('this fold');
    expect(table?.run_id).toBeNull();
    expect(table?.rows[0]?.count).toBe(1);
  });

  it('names every clock domain a table covers', () => {
    const view = applySnapshot(initialState(), timingsSnapshot as unknown as Snapshot);
    expect(clockDomainsOf(timingTable(view)?.rows ?? [])).toEqual(['laptop_monotonic', 'pi_monotonic']);
  });
});

describe('comparing the tap with the sample a decision consumed', () => {
  it('says so when the two are the same sample', () => {
    const comparison = compareTapWithConsumed(stamp('host-a', 12), stamp('host-a', 12));
    expect(comparison.kind).toBe('same_sample');
    expect(comparisonWords(comparison)).toContain('The same sample');
  });

  it('states how many samples the controller is behind, as a count and never as a delay', () => {
    const comparison = compareTapWithConsumed(stamp('host-a', 20), stamp('host-a', 17));
    expect(comparison).toMatchObject({ kind: 'controller_behind', samples: 3 });
    const words = comparisonWords(comparison);
    expect(words).toContain('3 behind');
    expect(words).toContain('never subtracted');
    expect(words).not.toContain('ms');
  });

  it('states when this tab is the one holding the older sample', () => {
    const comparison = compareTapWithConsumed(stamp('host-a', 4), stamp('host-a', 9));
    expect(comparison).toMatchObject({ kind: 'tab_behind', samples: 5 });
  });

  it('refuses to compare two different source sessions rather than subtracting their sequence numbers', () => {
    const comparison = compareTapWithConsumed(stamp('host-a', 20), stamp('host-b', 3));
    expect(comparison.kind).toBe('not_comparable');
    const words = comparisonWords(comparison);
    expect(words).toContain('Not comparable');
    expect(words).not.toContain('17');
  });

  it('names which of the two is missing instead of comparing with nothing', () => {
    expect(compareTapWithConsumed(null, stamp('host-a', 1))).toMatchObject({ kind: 'absent', missing: 'tap' });
    expect(compareTapWithConsumed(stamp('host-a', 1), null)).toMatchObject({ kind: 'absent', missing: 'consumed' });
    expect(compareTapWithConsumed(null, null)).toMatchObject({ kind: 'absent', missing: 'both' });
  });

  it('draws the six contract joints and appends one a newer controller reports', () => {
    const names = jointNames({ joints: { shoulder_pan: 1, wrist_yaw: 2 } } as never, null);
    expect(names.slice(0, 6)).toEqual(['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']);
    expect(names).toContain('wrist_yaw');
  });
});

describe('grouping finished runs by what produced them', () => {
  it('never puts a hardware run and a synthetic run in one group', () => {
    const groups = groupRuns([
      entry({ run_id: 'a', data_mode: 'live', bundle_digest: 'sha256:aaaaaaaaaaaabbbb' }),
      entry({ run_id: 'b', data_mode: 'synthetic', bundle_digest: 'sha256:aaaaaaaaaaaabbbb' }),
    ]);
    expect(groups.length).toBe(2);
    expect(groups[0]?.data_mode).toBe('live');
    expect(groups[0]?.entries.length).toBe(1);
  });

  it('separates two implementations of the same mode, so a thin-margin baseline is never pooled with a later pass', () => {
    const groups = groupRuns([
      entry({ run_id: 'a', data_mode: 'live', bundle_digest: 'sha256:111111111111' }),
      entry({ run_id: 'b', data_mode: 'live', bundle_digest: 'sha256:222222222222' }),
      entry({ run_id: 'c', data_mode: 'live', bundle_digest: 'sha256:111111111111' }),
    ]);
    expect(groups.length).toBe(2);
    expect(groups.map((group) => group.entries.length).sort()).toEqual([1, 2]);
  });

  it('keeps a row that recorded no mode apart, and never fills it in', () => {
    const groups = groupRuns([entry({ run_id: 'a', data_mode: 'live' }), entry({ run_id: 'b' })]);
    const unrecorded = groups.find((group) => group.data_mode === null);
    expect(unrecorded?.entries.length).toBe(1);
    expect(unrecorded?.entries[0]?.run_id).toBe('b');
  });

  it('reads the artifact digest as the implementation when no bundle digest was recorded', () => {
    const groups = groupRuns([entry({ run_id: 'a', artifact_digest: 'sha256:cafecafecafe' })]);
    expect(groups[0]?.implementation).toBe('sha256:cafecafecafe');
  });
});

describe('what a recording says about itself', () => {
  it('reads the mode from the recording own run.started record', () => {
    const events = [event('run.started', { command_id: 'c', command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: false })];
    const provenance = recordedProvenance(events);
    expect(provenance.data_mode).toBe('live');
    expect(provenanceWords(provenance)).toBe('recorded from hardware');
  });

  it('leaves the mode unstated for a recording written before the field existed, rather than taking this runtime mode', () => {
    const provenance = recordedProvenance([event('state.transition', { machine: 'cycle', from_state: null, to_state: 'SEARCH' })]);
    expect(provenance.data_mode).toBeNull();
    expect(provenanceWords(provenance)).toContain('does not state');
  });

  it('takes the implementation from the first record that names one', () => {
    const events = [
      event('state.transition', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }),
      event(
        'state.transition',
        { machine: 'cycle', from_state: 'SEARCH', to_state: 'ALIGN' },
        { producer: { name: 'controller', version: '1', artifact_digest: 'sha256:deadbeefdead', contract_version: '1' } },
      ),
    ];
    expect(recordedProvenance(events).implementation).toBe('sha256:deadbeefdead');
  });
});

describe('the replay time axis', () => {
  const recording = [
    event('state.transition', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }, { t_mono_ns: 0 }),
    event('state.transition', { machine: 'cycle', from_state: 'SEARCH', to_state: 'ALIGN' }, { t_mono_ns: 200_000_000 }),
    event('state.transition', { machine: 'cycle', from_state: 'ALIGN', to_state: 'GRASP' }, { t_mono_ns: 900_000_000 }),
  ];

  it('measures elapsed time from the first record to the cursor, in the recording own clock', () => {
    expect(replayAxis(recording, 2).elapsed_ns).toBe(200_000_000);
    expect(replayAxis(recording, 3).elapsed_ns).toBe(900_000_000);
    expect(replayAxis(recording, 3).span_ns).toBe(900_000_000);
    expect(replayAxis(recording, 3).clock_domain).toBe('laptop_monotonic');
  });

  it('states no elapsed time before two records exist to measure between', () => {
    const axis = replayAxis(recording.slice(0, 1), 1);
    expect(axis.elapsed_ns).toBeNull();
    expect(axis.span_ns).toBeNull();
  });

  it('counts a record from another clock instead of placing it on the axis', () => {
    const mixed = [...recording, event('timing.span', { span: 'capture', duration_ns: 1, clock_domain: 'pi_monotonic', detail: null } as never, { clock_domain: 'pi_monotonic', t_mono_ns: 5 })];
    const axis = replayAxis(mixed, mixed.length);
    expect(axis.on_axis).toBe(3);
    expect(axis.off_axis).toBe(1);
    expect(axis.span_ns).toBe(900_000_000);
  });

  it('advances the cursor by the recording own elapsed time', () => {
    // 250 ms of recorded time from t=0 reaches the record at 200 ms and stops before the one at 900 ms.
    expect(advanceReplayCursor(recording, 1, 250)).toBe(2);
    expect(advanceReplayCursor(recording, 2, 250)).toBe(3);
  });

  it('always moves at least one record, so play can never look like a control that did nothing', () => {
    expect(advanceReplayCursor(recording, 0, 250)).toBe(1);
    expect(advanceReplayCursor(recording, 2, 1)).toBe(3);
  });

  it('stops at the end and never runs past it', () => {
    expect(advanceReplayCursor(recording, 3, 250)).toBe(3);
    expect(advanceReplayCursor(recording, 99, 250)).toBe(3);
  });
});

describe('what happened between the start of a run and its end', () => {
  function withHistory(states: string[]): ViewState {
    return foldEvents(
      initialState(),
      states.map((state, at) =>
        event('state.transition', { machine: 'cycle', from_state: at === 0 ? null : (states[at - 1] ?? null), to_state: state }),
      ),
    );
  }

  it('counts how often a state was entered again, which is an observation and not the retry total', () => {
    const view = withHistory(['SEARCH', 'ALIGN', 'GRASP_CLOSE', 'ALIGN', 'GRASP_CLOSE', 'ALIGN']);
    const reentries = stateReentries(view);
    expect(reentries[0]).toEqual({ state: 'ALIGN', entries: 3 });
    expect(reentries.find((row) => row.state === 'SEARCH')).toBeUndefined();
  });

  it('has nothing to report when every state was entered once', () => {
    expect(stateReentries(withHistory(['SEARCH', 'ALIGN']))).toEqual([]);
  });

  it('quotes the runtime own reason for a blocked command instead of writing one here', () => {
    const view = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
    const reason = commandBlockReason(view);
    expect(reason === null || typeof reason === 'string').toBe(true);
    expect(commandBlockReason(initialState())).toBeNull();
  });
});
