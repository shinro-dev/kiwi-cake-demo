// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import liveDisconnected from '@kiwi/demo-contracts/fixtures/snapshot/live-disconnected.json';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import thinEvidence from '@kiwi/demo-contracts/fixtures/event/run.finished.placed-on-thin-evidence.json';
import placedShort from '@kiwi/demo-contracts/fixtures/event/run.finished.placed-short-of-the-clearance-target.json';
import type { PayloadRunFinished, Snapshot } from '@kiwi/demo-contracts';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import { applyEvent, applyResync, applySnapshot, foldEvents, initialState, markContractIncompatible } from './reducer';
import { overlayLayers } from './overlay';
import { parsePerception } from './observation';

let seq = 0;

function envelope(extra: Partial<EventEnvelope> = {}): EventEnvelope {
  seq += 1;
  return {
    schema_version: '1',
    seq,
    run_id: 'run-a',
    action_id: null,
    observation_id: null,
    producer: { name: 'test', version: '0', artifact_digest: null, contract_version: '1' },
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

function transition(to: string, from: string | null): ContractEvent {
  return event('state.transition', { machine: 'cycle', from_state: from, to_state: to });
}

function capture(observationId: string): ContractEvent {
  return event(
    'observation.captured',
    { source_role: 'wrist', media: 'frame', width_px: 640, height_px: 480, content_digest: null, source_stamp: null, retained: true },
    { observation_id: observationId },
  );
}

function predicate(name: string, value: boolean | null, observationIds: string[]): ContractEvent {
  return event('predicate.evaluated', {
    predicate: name,
    value,
    threshold: 3,
    measured: 30,
    unit: 'mm',
    evidence: [{ type: 'frame', observation_ids: observationIds, scalar_name: null }],
    consumed_source_stamp: null,
  });
}

function finished(payload: Partial<PayloadRunFinished>): ContractEvent {
  return event('run.finished', {
    outcome: 'placed_new',
    exit_status: 0,
    predicates: { object_secured: true, placed: true, clearance_target_met: true, base_returned: true },
    automatic_retries: 0,
    task_control_interventions: 0,
    evidence_complete: true,
    evidence_gaps: 0,
    ...payload,
  });
}

describe('state progression', () => {
  it('follows the controller state names and keeps the transition history', () => {
    const state = foldEvents(initialState(), [transition('SEARCH', null), transition('PREGRASP', 'SEARCH'), transition('ALIGN', 'PREGRASP')]);
    expect(state.machine?.state).toBe('ALIGN');
    expect(state.machine?.from_state).toBe('PREGRASP');
    expect(state.machine?.history.map((item) => item.state)).toEqual(['SEARCH', 'PREGRASP', 'ALIGN']);
  });

  it('carries an action through planned, started and completed without losing its parameters', () => {
    const state = foldEvents(initialState(), [
      event('action.planned', { action_type: 'pan_step', plan_only: false, parameters: [{ name: 'deg', value: -3.5, unit: 'deg' }] }, { action_id: 'a1' }),
      event('action.started', { action_type: 'pan_step' }, { action_id: 'a1' }),
      event('action.completed', { action_type: 'pan_step', result: 'ok', duration_ns: 1_500_000_000 }, { action_id: 'a1' }),
    ]);
    expect(state.current_action?.phase).toBe('completed');
    expect(state.current_action?.parameters).toEqual([{ name: 'deg', value: -3.5, unit: 'deg' }]);
    expect(state.current_action?.duration_ns).toBe(1_500_000_000);
  });

  it('records a refusal with its reason code', () => {
    const state = foldEvents(initialState(), [
      event('action.refused', { action_type: 'pan_step', before_motion: true, detail: 'a pan step must be non-zero' }, { action_id: 'a2', reason_code: 'step_out_of_bounds' }),
    ]);
    expect(state.current_action?.phase).toBe('refused');
    expect(state.current_action?.refusal?.reason_code).toBe('step_out_of_bounds');
    expect(state.current_action?.refusal?.before_motion).toBe(true);
  });

  it('clears the previous run detail when a new run starts', () => {
    const first = foldEvents(initialState(), [transition('SEARCH', null), predicate('cube_visible', true, ['obs-1'])]);
    expect(first.predicates.length).toBe(1);
    const second = applyEvent(first, event('run.started', { command_id: 'c1', command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: false }));
    expect(second.predicates).toEqual([]);
    expect(second.machine).toBeNull();
    expect(second.outcome.reported).toBeNull();
  });
});

describe('the decision frame', () => {
  it('is pinned by the predicate that consumed the evidence, not by the capture', () => {
    const state = foldEvents(initialState(), [
      capture('obs-1'),
      event('perception.result', { detector: 'cube-detector', observation_ids: ['obs-1'], result: 'detected', confidence: 0.7, measurements: [] }),
      predicate('cube_visible', true, ['obs-1']),
    ]);
    expect(state.decision_frame?.primary_observation_id).toBe('obs-1');
    expect(state.decision_frame?.consumed_by.by).toBe('predicate');
    expect(state.decision_frame?.perception?.payload.detector).toBe('cube-detector');
    expect(state.decision_frame?.capture?.source_role).toBe('wrist');
  });

  it('stays on the pinned frame while later observations are captured', () => {
    const pinned = foldEvents(initialState(), [capture('obs-1'), predicate('cube_visible', true, ['obs-1'])]);
    const pinnedAt = pinned.decision_frame?.pinned_at_seq;
    const later = foldEvents(pinned, [capture('obs-2'), capture('obs-3')]);
    expect(later.decision_frame?.primary_observation_id).toBe('obs-1');
    expect(later.decision_frame?.pinned_at_seq).toBe(pinnedAt);
  });

  it('moves only when a predicate or an action consumes different evidence', () => {
    const pinned = foldEvents(initialState(), [capture('obs-1'), predicate('cube_visible', true, ['obs-1']), capture('obs-2')]);
    const again = applyEvent(pinned, predicate('cube_visible', true, ['obs-1']));
    expect(again.decision_frame?.pinned_at_seq).toBe(pinned.decision_frame?.pinned_at_seq);
    const moved = applyEvent(again, predicate('cube_inside_pad', false, ['obs-2']));
    expect(moved.decision_frame?.primary_observation_id).toBe('obs-2');
    expect(moved.decision_frame?.pinned_at_seq).not.toBe(pinned.decision_frame?.pinned_at_seq);
  });

  it('is pinned by an action that names its evidence in the provenance', () => {
    const state = foldEvents(initialState(), [
      capture('obs-9'),
      event(
        'action.planned',
        { action_type: 'pan_step', plan_only: false, parameters: [] },
        { action_id: 'a1', provenance: { observation_ids: ['obs-9'], model_identity: null, calibration_digest: null, config_digest: null, module_generation: null, reconstructed: false } },
      ),
    ]);
    expect(state.decision_frame?.primary_observation_id).toBe('obs-9');
    expect(state.decision_frame?.consumed_by.by).toBe('action');
  });

  it('attaches a perception that arrives after the pin without moving the pin', () => {
    const pinned = foldEvents(initialState(), [capture('obs-1'), predicate('cube_visible', true, ['obs-1'])]);
    expect(pinned.decision_frame?.perception).toBeNull();
    const later = applyEvent(pinned, event('perception.result', { detector: 'cube-detector', observation_ids: ['obs-1'], result: 'detected', confidence: 0.7, measurements: [] }));
    expect(later.decision_frame?.perception?.payload.detector).toBe('cube-detector');
    expect(later.decision_frame?.pinned_at_seq).toBe(pinned.decision_frame?.pinned_at_seq);
  });
});

// A SCAN'S FUSED RESULT NAMES THE FRAMES IT WAS BUILT FROM, and it is not those frames' own perception. The scan
// (`scene-view`, `scene-block`, `scene-pad`) comes after the per-frame detections of the same frames; before this rule
// every `perception.result` replaced a frame's own, so the decision frame ended up drawn under the scene's summary.
describe('a scene result that names a frame', () => {
  const own = () => event('perception.result', { detector: 'cube-detector', observation_ids: ['obs-1'], result: 'detected', confidence: 0.7, measurements: [] });
  const scene = (detector: string) =>
    event('perception.result', { detector, observation_ids: ['obs-1', 'obs-2'], result: 'detected', confidence: null, measurements: [] });

  it('keeps the frame its own perception, on the pinned frame and in the per-frame map', () => {
    const state = foldEvents(initialState(), [capture('obs-1'), own(), predicate('cube_visible', true, ['obs-1']), scene('scene-block')]);
    expect(state.decision_frame?.perception?.payload.detector).toBe('cube-detector');
    expect(state.perception_by_observation['obs-1']?.payload.detector).toBe('cube-detector');
    expect(state.last_perception?.payload.detector).toBe('cube-detector');
  });

  it('is filed under a frame that has no perception of its own', () => {
    const state = foldEvents(initialState(), [capture('obs-1'), own(), scene('scene-view')]);
    expect(state.perception_by_observation['obs-2']?.payload.detector).toBe('scene-view');
  });

  it('never keeps an older per-frame result over a newer per-frame one', () => {
    const newer = () => event('perception.result', { detector: 'pad-detector', observation_ids: ['obs-1'], result: 'detected', confidence: 0.9, measurements: [] });
    const state = foldEvents(initialState(), [capture('obs-1'), own(), predicate('cube_visible', true, ['obs-1']), newer()]);
    expect(state.decision_frame?.perception?.payload.detector).toBe('pad-detector');
    expect(state.perception_by_observation['obs-1']?.payload.detector).toBe('pad-detector');
  });

  it('stands as the latest perception when nothing else has been perceived', () => {
    const state = foldEvents(initialState(), [capture('obs-1'), scene('scene-pad')]);
    expect(state.last_perception?.payload.detector).toBe('scene-pad');
  });
});

describe('per-layer overlay validity', () => {
  const withPadFrame = {
    pad: { detected: true, corners_px: [[0, 0], [10, 0], [10, 10], [0, 10]] },
    pad_by_template: { pad_frame_valid: true, reason: null },
    cube: { detected: true, box_px: [1, 1, 5, 5], contact_px: [3, 5], mask_polygon_px: [[1, 1], [5, 1], [5, 5]] },
    jaw_window_px: [0, 0, 8, 8],
    landmarks_px: [[2, 2]],
  };

  it('keeps every layer when the pad frame is valid', () => {
    const model = overlayLayers(parsePerception(withPadFrame));
    expect(model.pad_frame_valid).toBe(true);
    for (const layer of Object.values(model.layers)) expect(layer.valid).toBe(true);
    expect(model.homography?.derived).toBe(true);
  });

  it('keeps the cube layers and drops the pad layers when the pad frame is missing', () => {
    const dto = parsePerception({
      ...withPadFrame,
      pad_by_template: { pad_frame_valid: false, reason: 'too few landmarks matched at this range' },
    });
    const model = overlayLayers(dto);
    expect(model.pad_frame_valid).toBe(false);
    expect(model.layers.cube_box.valid).toBe(true);
    expect(model.layers.cube_contact.valid).toBe(true);
    expect(model.layers.cube_mask.valid).toBe(true);
    expect(model.layers.jaw_window.valid).toBe(true);
    expect(model.layers.pad_outline.valid).toBe(false);
    expect(model.layers.pad_landmarks.valid).toBe(false);
    expect(model.layers.pad_projection.valid).toBe(false);
    expect(model.homography).toBeNull();
    expect(model.layers.pad_outline.reason).toBe('too few landmarks matched at this range');
  });

  it('gives every invalid layer a reason', () => {
    const model = overlayLayers(null);
    for (const layer of Object.values(model.layers)) {
      expect(layer.valid).toBe(false);
      expect(layer.reason).not.toBeNull();
    }
  });

  it('reports the pad projection as unavailable when the pad frame is valid but nothing places it', () => {
    const model = overlayLayers(parsePerception({ ...withPadFrame, pad: { detected: true }, pad_by_template: { pad_frame_valid: true, reason: null } }));
    expect(model.layers.pad_projection.valid).toBe(false);
    expect(model.layers.pad_outline.valid).toBe(false);
    expect(model.layers.cube_box.valid).toBe(true);
  });
});

describe('outcome mapping', () => {
  it('confirms placed_new only when all four predicates measured true', () => {
    const state = applyEvent(initialState(), finished({}));
    expect(state.outcome.reported).toBe('placed_new');
    expect(state.outcome.confirmed).toBe('placed_new');
  });

  it('never confirms placed_new when a predicate was not evaluated', () => {
    const state = applyEvent(initialState(), finished({ predicates: { object_secured: true, placed: true, clearance_target_met: null, base_returned: true } }));
    expect(state.outcome.reported).toBe('placed_new');
    expect(state.outcome.confirmed).toBeNull();
  });

  it('never confirms placed_new when a predicate measured false', () => {
    const state = applyEvent(initialState(), finished({ predicates: { object_secured: true, placed: true, clearance_target_met: false, base_returned: true } }));
    expect(state.outcome.confirmed).toBeNull();
  });

  it('keeps the physical outcome and the evidence completeness apart', () => {
    // The fixture is a real contract record: placed_new on thin evidence.
    const state = applyEvent(initialState(), { ...envelope(), kind: 'run.finished', payload: thinEvidence.payload as PayloadRunFinished } as ContractEvent);
    expect(state.outcome.reported).toBe('placed_new');
    expect(state.outcome.confirmed).toBe('placed_new');
    expect(state.outcome.evidence_complete).toBe(false);
    expect(state.outcome.evidence_gaps).toBe(2);
  });

  it('keeps the placement and the clearance target apart', () => {
    // The shape of the 2026-09-22 physical run: the cube was placed and the base returned, and the footprint clearance
    // fell 2.39 mm short of its target. The runtime used to collapse that to `unknown`, which the contract reserves for
    // a run nothing established. The placement is reported; the miss travels as its own false predicate.
    const state = applyEvent(initialState(), { ...envelope(), kind: 'run.finished', payload: placedShort.payload as PayloadRunFinished } as ContractEvent);
    expect(state.outcome.reported).toBe('placed_new');
    expect(state.outcome.confirmed).toBeNull();
    expect(state.outcome.predicates?.placed).toBe(true);
    expect(state.outcome.predicates?.base_returned).toBe(true);
    expect(state.outcome.predicates?.object_secured).toBe(true);
    expect(state.outcome.predicates?.clearance_target_met).toBe(false);
    // The evidence gap of that run was a source stamp missing before any motion, six minutes before the clearance was
    // measured. It is a separate fact and stays exactly where it was.
    expect(state.outcome.evidence_complete).toBe(false);
    expect(state.outcome.evidence_gaps).toBe(1);
    expect(state.outcome.reason_code).toBeNull();
  });

  it('carries a refusal outcome through without inventing predicates', () => {
    const state = applyEvent(initialState(), finished({ outcome: 'refused', exit_status: 3, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null } }));
    expect(state.outcome.reported).toBe('refused');
    expect(state.outcome.confirmed).toBeNull();
    expect(state.outcome.predicates?.placed).toBeNull();
  });
});

describe('stream gaps', () => {
  it('records an announced resync as a declared gap', () => {
    const state = applyResync(initialState(), { retained_from_seq: 40, missed: [12, 39] });
    expect(state.retained_from_seq).toBe(40);
    expect(state.stream_gaps).toEqual([{ from_seq: 12, to_seq: 39, declared: true }]);
  });

  it('records a seq that skips ahead as an undeclared gap rather than skipping it in silence', () => {
    const first = applyEvent(initialState(), transition('SEARCH', null));
    const jumped = applyEvent(first, { ...transition('PREGRASP', 'SEARCH'), seq: (first.last_event_seq as number) + 5 } as ContractEvent);
    expect(jumped.stream_gaps).toEqual([{ from_seq: (first.last_event_seq as number) + 1, to_seq: (first.last_event_seq as number) + 4, declared: false }]);
  });

  it('does not report an announced gap a second time when the seq jump arrives', () => {
    const first = applyEvent(initialState(), transition('SEARCH', null));
    const from = (first.last_event_seq as number) + 1;
    const announced = applyResync(first, { retained_from_seq: from + 4, missed: [from, from + 3] });
    expect(announced.stream_gaps.length).toBe(1);
    const afterJump = applyEvent(announced, { ...transition('PREGRASP', 'SEARCH'), seq: from + 4 } as ContractEvent);
    expect(afterJump.stream_gaps.length).toBe(1);
    expect(afterJump.stream_gaps[0]?.declared).toBe(true);
  });

  it('does not invent a gap for consecutive events', () => {
    const state = foldEvents(initialState(), [transition('SEARCH', null), transition('PREGRASP', 'SEARCH')]);
    expect(state.stream_gaps).toEqual([]);
  });

  it('counts dropped telemetry separately from evidence gaps', () => {
    const state = foldEvents(initialState(), [
      event('telemetry.dropped', { count: 3, stage: 'client_stream', first_seq: 10, last_seq: 12 }),
      event('evidence.gap', { gap: 'frame_missing', detail: 'no frame was kept', observation_ids: [] }),
    ]);
    expect(state.evidence.dropped_telemetry).toBe(3);
    expect(state.evidence.gaps).toBe(1);
    expect(state.evidence.complete).toBe(false);
    expect(state.evidence_gap_events.length).toBe(1);
  });
});

describe('data mode and the link', () => {
  it('shows a live view whose link dropped as live, never as replay', () => {
    const state = applySnapshot(initialState(), liveDisconnected as unknown as Snapshot);
    expect(state.data_mode).toBe('live');
    expect(state.link.state).toBe('disconnected');
    expect(state.link.receive_age_ms).toBe(9000);
  });

  it('takes the mode from the snapshot and nothing else', () => {
    const state = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
    expect(state.data_mode).toBe('synthetic');
    expect(state.link.state).toBe('connected');
    expect(state.owner.kind).toBe('autonomous');
    expect(state.active_run?.state).toBe('SEARCH');
  });

  it('counts a runtime restart and clears the folded run detail', () => {
    const first = applySnapshot(foldEvents(initialState(), [transition('SEARCH', null)]), runningSnapshot as unknown as Snapshot);
    expect(first.machine?.state).toBe('SEARCH');
    const restarted = applySnapshot(first, { ...(runningSnapshot as unknown as Snapshot), runtime_session: 'another-session' });
    expect(restarted.counters.runtime_restarts).toBe(1);
    expect(restarted.machine).toBeNull();
  });
});

describe('the contract version', () => {
  it('makes the tab read-only and says why', () => {
    const state = markContractIncompatible(initialState(), 'contract_incompatible');
    expect(state.contract_compatible).toBe(false);
    expect(state.read_only_reason).toBe('contract_incompatible');
  });
});

describe('a cursor from a runtime session that was replaced', () => {
  // demo_runtime.journal.EventLog.subscribe answers a cursor it cannot serve at all with {missed: null,
  // reason: "cursor_ahead"}: the cursor belongs to a log this runtime is not serving. The client used to read that
  // line as malformed and go on counting parse failures, which is the reconnect this whole path exists for.
  const CURSOR_AHEAD = { retained_from_seq: 8, missed: null, reason: 'cursor_ahead' } as const;

  it('restarts the fold instead of folding a new log against the old log s sequence', () => {
    const folded = foldEvents(initialState(), [transition('SEARCH', null), predicate('cube_visible', true, ['obs-1'])]);
    expect(folded.machine?.state).toBe('SEARCH');
    const resynced = applyResync(folded, CURSOR_AHEAD);
    expect(resynced.machine).toBeNull();
    expect(resynced.predicates).toEqual([]);
    expect(resynced.last_event_seq).toBeNull();
    expect(resynced.counters.cursor_replacements).toBe(1);
  });

  it('moves the cursor back to what the runtime says it can still serve', () => {
    const folded = foldEvents(initialState(), [transition('SEARCH', null)]);
    const resynced = applyResync(folded, CURSOR_AHEAD);
    expect(resynced.seq_cursor).toBe(7);
    expect(resynced.retained_from_seq).toBe(8);
  });

  it('records what the new log wrote before this tab could see it as a declared gap', () => {
    const resynced = applyResync(initialState(), CURSOR_AHEAD);
    expect(resynced.stream_gaps).toEqual([{ from_seq: 1, to_seq: 7, declared: true }]);
  });

  it('claims no gap at all when the new log has dropped nothing', () => {
    const resynced = applyResync(initialState(), { retained_from_seq: 1, missed: null, reason: 'cursor_ahead' });
    expect(resynced.stream_gaps).toEqual([]);
    expect(resynced.seq_cursor).toBe(0);
  });

  it('folds the new log from its own first record after the resync', () => {
    const folded = foldEvents(initialState(), [transition('SEARCH', null), transition('PREGRASP', 'SEARCH')]);
    const resynced = applyResync(folded, { retained_from_seq: 1, missed: null, reason: 'cursor_ahead' });
    const next = applyEvent(resynced, { ...transition('CARRY', null), seq: 1 } as ContractEvent);
    expect(next.machine?.state).toBe('CARRY');
    expect(next.counters.duplicate_records).toBe(0);
  });

  it('does not let the next snapshot clear the new log s records a second time', () => {
    const RUNNING = runningSnapshot as unknown as Snapshot;
    const folded = applySnapshot(foldEvents(initialState(), [transition('SEARCH', null)]), { ...RUNNING, runtime_session: 'runtime-session-old' });
    const resynced = applyResync(folded, { retained_from_seq: 1, missed: null, reason: 'cursor_ahead' });
    // These belong to the log the runtime is serving NOW: they arrived after it said this tab's cursor did not.
    const newLog = applyEvent(resynced, { ...transition('CARRY', null), seq: 1 } as ContractEvent);
    const named = applySnapshot(newLog, RUNNING);
    expect(named.machine?.state).toBe('CARRY');
    expect(named.runtime_session).toBe(RUNNING.runtime_session);
    // The replacement was counted where it was observed, on the stream, and is not counted a second time here.
    expect(named.counters.cursor_replacements).toBe(1);
    expect(named.counters.runtime_restarts).toBe(0);
  });
});

describe('a record the fold has already seen', () => {
  it('is counted and ignored, so a replayed gap is not counted twice', () => {
    const gap = event('evidence.gap', { gap: 'frame_missing', detail: 'no frame was kept', observation_ids: [] });
    const once = applyEvent(initialState(), gap);
    const twice = applyEvent(once, gap);
    expect(twice.evidence.gaps).toBe(1);
    expect(twice.evidence_gap_events.length).toBe(1);
    expect(twice.counters.duplicate_records).toBe(1);
  });

  it('does not re-append to the recent records or re-count dropped telemetry', () => {
    const dropped = event('telemetry.dropped', { count: 4, stage: 'client_stream', first_seq: 1, last_seq: 4 });
    const state = applyEvent(applyEvent(initialState(), dropped), dropped);
    expect(state.evidence.dropped_telemetry).toBe(4);
    expect(state.recent_events.length).toBe(1);
  });
});

describe('a snapshot that arrives after records it does not contain', () => {
  const RUNNING = runningSnapshot as unknown as Snapshot;

  it('is not applied at all, and is counted', () => {
    const first = applySnapshot(initialState(), RUNNING);
    const ahead = applyEvent(first, { ...transition('CARRY', 'SEARCH'), seq: RUNNING.seq_cursor + 4 } as ContractEvent);
    const late = applySnapshot(ahead, { ...RUNNING, link: { state: 'disconnected', source_stamp: null, receive_age_ms: 9000 } });
    expect(late.link.state).toBe('connected');
    expect(late.counters.stale_snapshots).toBe(1);
    expect(late.machine?.state).toBe('CARRY');
  });

  it('cannot put back an evidence gap count the stream has already moved past', () => {
    const first = applySnapshot(initialState(), RUNNING);
    let ahead = applyEvent(first, {
      ...event('evidence.gap', { gap: 'frame_missing', detail: 'no frame was kept', observation_ids: [] }),
      seq: RUNNING.seq_cursor + 5,
    } as ContractEvent);
    ahead = { ...ahead, evidence: { ...ahead.evidence, gaps: 1, complete: false } };
    const late = applySnapshot(ahead, RUNNING);
    expect(late.evidence.complete).toBe(false);
    expect(late.evidence.gaps).toBe(1);
  });

  it('is applied in full when it is not behind the fold, so the runtime stays the authority', () => {
    const first = applySnapshot(initialState(), RUNNING);
    const later = applySnapshot(first, {
      ...RUNNING,
      seq_cursor: RUNNING.seq_cursor + 10,
      link: { state: 'disconnected', source_stamp: null, receive_age_ms: 9000 },
    });
    expect(later.link.state).toBe('disconnected');
    expect(later.seq_cursor).toBe(RUNNING.seq_cursor + 10);
    expect(later.counters.stale_snapshots).toBe(0);
  });

  it('never moves the cursor backwards', () => {
    const first = applySnapshot(initialState(), RUNNING);
    const ahead = applyEvent(first, { ...transition('CARRY', 'SEARCH'), seq: RUNNING.seq_cursor + 3 } as ContractEvent);
    expect(applySnapshot(ahead, RUNNING).seq_cursor).toBe(RUNNING.seq_cursor + 3);
  });
});

describe('the outcome of a run that finished before the run this fold is in', () => {
  const RUNNING = runningSnapshot as unknown as Snapshot;
  const idle = (cursor: number, lastRunId: string, finishedSeq: number, result: Partial<PayloadRunFinished>): Snapshot => ({
    ...RUNNING,
    seq_cursor: cursor,
    active_run: null,
    last_outcome: {
      run_id: lastRunId,
      finished_seq: finishedSeq,
      result: { outcome: 'failed', exit_status: 5, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, ...result },
    },
  });

  it('is never taken into the outcome of a later run the fold has started and not finished', () => {
    const base = applySnapshot(initialState(), { ...RUNNING, seq_cursor: 1, active_run: null, last_outcome: null });
    let state = foldEvents(base, [
      event('run.started', { command_id: 'c-a', command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false }, { run_id: 'run-a', seq: 100 }),
      event('run.finished', { outcome: 'failed', exit_status: 5, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' }, { run_id: 'run-a', seq: 101 }),
      event('run.started', { command_id: 'c-b', command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false }, { run_id: 'run-b', seq: 102 }),
      { ...transition('SEARCH', null), run_id: 'run-b', seq: 103 } as ContractEvent,
    ]);
    // The poll's snapshot names run-a as the newest FINISHED run and no run in progress yet: run-b is not its run.
    state = applySnapshot(state, idle(103, 'run-a', 101, { reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' }));
    expect(state.outcome.run_id).toBeNull();
    expect(state.outcome.reported).toBeNull();
    expect(state.previous_outcome?.run_id).toBe('run-a');
  });

  it("is taken when it is the started run's own end, which the stream has not delivered yet", () => {
    const base = applySnapshot(initialState(), { ...RUNNING, seq_cursor: 1, active_run: null, last_outcome: null });
    let state = foldEvents(base, [event('run.started', { command_id: 'c-b', command_type: 'start_cycle', data_mode: 'synthetic', plan_only: false, holding: false }, { run_id: 'run-b', seq: 102 })]);
    state = applySnapshot(state, idle(110, 'run-b', 109, { reason_code: 'align_cube_not_detected', phase: 'ALIGN' }));
    expect(state.outcome.run_id).toBe('run-b');
    expect(state.outcome.reason_code).toBe('align_cube_not_detected');
  });
});

describe('the sessions a fold contains', () => {
  it('records each producer session with the span of mission sequence numbers it occupies', () => {
    const state = foldEvents(initialState(), [transition('SEARCH', null), transition('PREGRASP', 'SEARCH')]);
    expect(state.source_sessions.length).toBe(1);
    expect(state.source_sessions[0]?.producer).toBe('test');
    expect(state.source_sessions[0]?.session).toBe('session-a');
    expect(state.source_sessions[0]?.records).toBe(2);
  });

  it('counts a producer that comes back with another session, and keeps folding its records', () => {
    const first = applyEvent(initialState(), transition('SEARCH', null));
    const second = applyEvent(first, event('state.transition', { machine: 'cycle', from_state: null, to_state: 'CARRY' }, { source_session: 'session-b' }));
    expect(second.counters.source_session_changes).toBe(1);
    expect(second.source_sessions.map((item) => item.session)).toEqual(['session-a', 'session-b']);
    // A controller session is minted per run, so a record from another one is NOT thrown away: refusing it would
    // throw away every record the controller ever writes.
    expect(second.machine?.state).toBe('CARRY');
  });

  it('never lets a source sequence number touch the mission cursor', () => {
    const stamp = { session: 'host-session-a', seq: 99_000, sample_mono_ns: 5, clock_domain: 'pi_monotonic' as const };
    const state = applyEvent(
      initialState(),
      event('measured.state', {
        joints: { shoulder_pan: 0.1, shoulder_lift: null, elbow_flex: null, wrist_flex: null, wrist_roll: null, gripper: null },
        base_velocity: { x_mps: null, y_mps: null, theta_dps: null },
        consumed_source_stamp: stamp,
      }),
    );
    expect(state.measured_state?.consumed_source_stamp.seq).toBe(99_000);
    expect(state.seq_cursor).toBeLessThan(99_000);
    expect(state.stream_gaps).toEqual([]);
  });
});

describe('a decision that names what it consumed', () => {
  it('pins the frame from the action s own evidence_refs rather than from its provenance', () => {
    const state = foldEvents(initialState(), [
      capture('obs-named'),
      event(
        'action.planned',
        { action_type: 'place', plan_only: false, parameters: [], evidence_refs: ['obs-named'] },
        { action_id: 'a9', provenance: { observation_ids: ['obs-other'], model_identity: null, calibration_digest: null, config_digest: null, module_generation: null, reconstructed: false } },
      ),
    ]);
    expect(state.decision_frame?.observation_ids).toEqual(['obs-named']);
    expect(state.decision_frame?.consumed_by.by).toBe('action');
  });

  it('lets a refusal name what it looked at, and leaves the frame alone when it names nothing', () => {
    const pinned = foldEvents(initialState(), [capture('obs-1'), predicate('clearance', true, ['obs-1'])]);
    const silent = applyEvent(pinned, event('action.refused', { action_type: 'place', before_motion: true, detail: 'no reachable placement' }, { action_id: 'r1' }));
    expect(silent.decision_frame?.observation_ids).toEqual(['obs-1']);
    const naming = applyEvent(
      silent,
      event('action.refused', { action_type: 'place', before_motion: true, detail: 'no reachable placement', evidence_refs: ['obs-2'] }, { action_id: 'r2' }),
    );
    expect(naming.decision_frame?.observation_ids).toEqual(['obs-2']);
    expect(naming.decision_frame?.consumed_by.name).toBe('place');
  });
});

describe('the picture under a pinned decision', () => {
  it('binds the capture record to the frame already pinned on it, without moving the pin', () => {
    const pinnedFirst = applyEvent(initialState(), predicate('clearance', true, ['obs-late']));
    expect(pinnedFirst.decision_frame?.capture).toBeNull();
    const pinnedAt = pinnedFirst.decision_frame?.pinned_at_seq;
    const bound = applyEvent(pinnedFirst, capture('obs-late'));
    expect(bound.decision_frame?.capture?.observation_id).toBe('obs-late');
    expect(bound.decision_frame?.capture?.width_px).toBe(640);
    expect(bound.decision_frame?.pinned_at_seq).toBe(pinnedAt);
  });

  it('does not let another observation s capture reach the pinned frame', () => {
    const pinned = applyEvent(initialState(), predicate('clearance', true, ['obs-mine']));
    const other = applyEvent(pinned, capture('obs-theirs'));
    expect(other.decision_frame?.primary_observation_id).toBe('obs-mine');
    expect(other.decision_frame?.capture).toBeNull();
  });
});

describe('what a predicate record says about itself', () => {
  it('carries the recorded state, its own reason and the rule that decided it', () => {
    const state = applyEvent(
      initialState(),
      event('predicate.evaluated', {
        predicate: 'clearance_target_met',
        value: null,
        state: 'unavailable',
        reason_code: 'no_frame_was_kept',
        rule_ref: 'clearance_rule',
        rule_digest: `sha256:${'b2'.padStart(64, '0')}`,
        threshold: 3,
        measured: null,
        unit: 'mm',
        evidence: [],
        consumed_source_stamp: null,
      }),
    );
    const view = state.predicates[0];
    expect(view?.state).toBe('unavailable');
    expect(view?.reason_code).toBe('no_frame_was_kept');
    expect(view?.rule_ref).toBe('clearance_rule');
    // The payload's reason is not the envelope's, and the two are kept apart.
    expect(view?.value).toBeNull();
  });

  it('leaves the state null on a record written before the field existed, rather than inventing one', () => {
    const state = applyEvent(initialState(), predicate('cube_visible', true, ['obs-1']));
    expect(state.predicates[0]?.state).toBeNull();
    expect(state.predicates[0]?.reason_code).toBeNull();
    expect(state.predicates[0]?.rule_digest).toBeNull();
  });
});

describe('durations measured in two clocks', () => {
  function timing(span: string, durationNs: number, domain: 'laptop_monotonic' | 'pi_monotonic'): ContractEvent {
    return event('timing.span', { span, duration_ns: durationNs, clock_domain: domain, detail: null } as never);
  }

  it('keeps one span name measured in two clocks as two rows and never adds them together', () => {
    // The controller does exactly this: it measures capture on this computer and also on the robot computer.
    const state = foldEvents(initialState(), [
      timing('capture', 100_000_000, 'laptop_monotonic'),
      timing('capture', 30_000_000, 'pi_monotonic'),
    ]);
    expect(state.timings.length).toBe(2);
    const laptop = state.timings.find((row) => row.clock_domain === 'laptop_monotonic');
    const pi = state.timings.find((row) => row.clock_domain === 'pi_monotonic');
    expect(laptop?.total_ns).toBe(100_000_000);
    expect(pi?.total_ns).toBe(30_000_000);
    expect(state.timings.some((row) => row.clock_domain === 'unknown')).toBe(false);
  });

  it('accumulates a repeat inside one clock and moves the window it was measured over', () => {
    const state = foldEvents(initialState(), [
      timing('motion', 10_000_000, 'laptop_monotonic'),
      timing('motion', 40_000_000, 'laptop_monotonic'),
    ]);
    const row = state.timings[0];
    expect(row?.count).toBe(2);
    expect(row?.total_ns).toBe(50_000_000);
    expect(row?.max_ns).toBe(40_000_000);
    expect(row?.last_ns).toBe(40_000_000);
    expect(row?.first_seq).toBeLessThan(row?.last_seq ?? 0);
  });

  it('carries what the runtime aggregated beside the fold, without merging the two windows', () => {
    const state = applySnapshot(foldEvents(initialState(), [timing('motion', 10_000_000, 'laptop_monotonic')]), {
      ...(runningSnapshot as unknown as Snapshot),
      timings: { run_id: 'run-a', spans: [{ span: 'motion', clock_domain: 'laptop_monotonic', count: 9, total_ns: 90_000_000, max_ns: 20_000_000, last_ns: 11_000_000, first_seq: 2, last_seq: 44 }] },
    });
    expect(state.runtime_timings?.spans[0]?.count).toBe(9);
    expect(state.timings[0]?.count).toBe(1);
  });

  it('reports no run evidence at all from a runtime that sends none, rather than an empty one', () => {
    const state = applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
    expect(state.run_evidence).toBeNull();
    expect(state.runtime_timings).toBeNull();
  });
});
