// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE OUTCOME CARD (spec sections 3.2, 5.7.1, 6.2 and 6.7; audit C5, C6, T5, top 6). It states the mission's verdict and
// its run's, each in its own word; why the run stopped with the stop's own measurement; where the robot was left, READ
// from the record and never asked; the runtime's step list exactly, in its order; and the mission's end time, derived
// from its last run's finish and labelled so. An ending stays expanded until Acknowledge is pressed on this display; a
// new attempt or a new mission ending comes back expanded; a storage that fails leaves it expanded after a reload.
// The record is mission-f65c3cf8e99f of 2026-09-27.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import type { HistoryEntry } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import type { MissionAttemptRecord, MissionRecord } from '../model/missionRecord';
import { ACKNOWLEDGED_KEY, ackKeyOf, outcomeCard } from '../model/outcomeCard';
import type { AckRecord, OutcomeInput } from '../model/outcomeCard';
import { ConsoleStore } from '../store/store';
import { OutcomeCard } from './OutcomeCard';
import { wash } from './theme';
import { TONE_COLOUR } from './TopBar';

const RUN1 = 'console-20260927T141638Z-m2fc135a';
const RUN2 = 'console-20260927T142146Z-m6ed2dd9';

function ev(seq: number, kind: string, t: string, payload: Record<string, unknown>, extra: { action_id?: string; reason_code?: string } = {}): ContractEvent {
  return {
    schema_version: '1', seq, run_id: RUN2, action_id: extra.action_id ?? null, observation_id: null,
    producer: { name: 'demo-runtime', version: '0.1.0', artifact_digest: null, contract_version: '1' },
    source_session: 'rt', t_mono_ns: seq, clock_domain: 'laptop_monotonic', t_wall_utc: t, reason_code: extra.reason_code ?? null, provenance: null, kind, payload,
  } as unknown as ContractEvent;
}

const RUN2_EVENTS: ContractEvent[] = [
  ev(3626, 'run.started', '2026-09-27T14:21:46.510Z', { command_type: 'start_cycle' }),
  ev(3825, 'predicate.evaluated', '2026-09-27T14:24:08.0067Z', { predicate: 'straddle_far_edge_row', value: false, threshold: 420, measured: 333, unit: 'px', evidence: [{ type: 'frame', observation_ids: ['obs-20260927T142354Z-wrist-0001'] }] }),
  ev(3851, 'state.transition', '2026-09-27T14:24:26.235895Z', { machine: 'cycle', from_state: 'STRADDLING', to_state: 'GRASP_CLOSE' }),
  ev(3892, 'action.planned', '2026-09-27T14:24:38.287829Z', { action_type: 'arm_pose', parameters: [{ name: 'label', value: 'CLEARANCE lift', unit: null }] }, { action_id: 'act-0007' }),
  ev(3902, 'predicate.evaluated', '2026-09-27T14:24:39.397375Z', { predicate: 'base_zero', value: false, threshold: 3, measured: 0, unit: 'reads' }, { action_id: 'act-0007' }),
  ev(3903, 'measured.state', '2026-09-27T14:24:39.412961Z', { joints: { gripper: 68.11594202898551 }, base_velocity: { x_mps: -6.98e-19, y_mps: 0.0025566346464760688, theta_dps: 1.171875 } }, { action_id: 'act-0007' }),
  ev(3904, 'action.refused', '2026-09-27T14:24:39.415011Z', { action_type: 'arm_pose', detail: 'base velocity not confirmed zero before an arm segment: stopping all motion' }, { action_id: 'act-0007', reason_code: 'base_not_zero_before_segment' }),
  ev(3906, 'run.finished', '2026-09-27T14:24:40.112Z', { outcome: 'stopped', exit_status: 4, reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE', failure: { detail: 'base velocity not confirmed zero before an arm segment: stopping all motion', measurements: [], observation_ids: [] } }),
];

const ATTEMPT: MissionAttemptRecord = {
  attempt: 1, run_id: RUN2, park_run_id: null, outcome_class: 'stopped', reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE', measurements: [], observation_ids: [],
  stop_state: { holding: true, base_ahead: false, moved: true }, next_actions: ['open_gripper_by_hand', 'return_arm', 'start'], budget_left: 1, t_wall_utc: '2026-09-27T14:24:40.265Z',
};

function mission(patch: Partial<MissionRecord> = {}, attempt: MissionAttemptRecord = ATTEMPT): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-f65c3cf8e99f', state: 'FAILED', data_mode: 'live', performs_motion: true, execution_label: null,
    started_wall_utc: '2026-09-27T14:16:38.847Z', selected_target: 'cube', waiting_reason: null, detail: 'run_outcome:stopped:base_not_zero_before_segment',
    stop_requested: false, stop_requested_wall_utc: null,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: [RUN1, RUN2], attempts: [attempt], retries: [], park_runs: [] }],
    activations: [], hold: null, next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
    ...patch,
  };
}

const HISTORY = [
  { run_id: RUN1, started_wall_utc: '2026-09-27T14:16:38.938Z', finished_wall_utc: '2026-09-27T14:17:08.967Z', outcome: 'failed' },
  { run_id: RUN2, started_wall_utc: '2026-09-27T14:21:46.005Z', finished_wall_utc: '2026-09-27T14:24:40.114Z', outcome: 'stopped' },
] as unknown as HistoryEntry[];

function input(patch: Partial<OutcomeInput> = {}): OutcomeInput {
  return { mission: mission(), missionError: null, history: HISTORY, runEvents: { [RUN2]: RUN2_EVENTS }, activeRunId: null, acknowledgements: [], capability: null, drawnRunEvents: {}, ...patch };
}

function render(model: ReturnType<typeof outcomeCard>): string {
  return renderToStaticMarkup(createElement(OutcomeCard, { model, returnArm: createElement('button', { 'data-control': 'return_arm', 'aria-disabled': 'false' }, 'Return arm'), retrying: false, onRetry: () => undefined, onAcknowledge: () => undefined }));
}

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');
}

describe('the outcome card of an ended mission', () => {
  it('states the mission and its run each in its own word, and the stop with its own measurement', () => {
    const markup = render(outcomeCard(input()));
    const words = text(markup);
    expect(markup).toContain('data-verdict="failed"');
    expect(markup).toContain('data-run-verdict="stopped_by_controller"');
    expect(words).toContain('Mission failed');
    expect(words).toContain(`Run 2 of 2 stopped by the controller, ${RUN2}`);
    expect(words).toContain('base_zero: 0 reads against 3 reads, 14:24:39Z');
    expect(words).toContain('Base velocity in that reading: x 0.000 m/s, y 0.003 m/s, rotation 1.17 deg/s');
    expect(words).not.toContain('straddle_far_edge_row');
    expect(words).not.toContain('px');
    expect(markup).not.toContain('data-failure-frame');
    expect(words).not.toContain('FAILED');
  });

  it('reads holding from the record and never asks: holding true is "Holding the block" with the gripper reading', () => {
    const markup = render(outcomeCard(input()));
    expect(markup).toContain('data-holding="true"');
    expect(text(markup)).toContain('Holding the block');
    expect(text(markup)).toContain('gripper 68.12');
    expect(text(markup)).not.toContain('?');
  });

  it('says it is not known whether the gripper holds the block when the record does not say', () => {
    const unknown = render(outcomeCard(input({ mission: mission({}, { ...ATTEMPT, stop_state: { holding: null, base_ahead: false, moved: true } }) })));
    expect(unknown).toContain('data-holding="unknown"');
    expect(text(unknown)).toContain('Not known whether the gripper holds the block');
    const empty = render(outcomeCard(input({ mission: mission({}, { ...ATTEMPT, stop_state: { holding: false, base_ahead: false, moved: true } }) })));
    expect(text(empty)).toContain('Gripper empty');
  });

  it('says what the stop state records about the base and about motion, never a position (verifier V5)', () => {
    const stopState = (base_ahead: boolean | null, moved: boolean | null): string =>
      text(render(outcomeCard(input({ mission: mission({}, { ...ATTEMPT, stop_state: { holding: true, base_ahead, moved } }) }))));
    // base_ahead is "no BASE_ADVANCE outstanding" and moved is "an action.started was recorded" (mission_execution.py).
    const home = stopState(false, true);
    expect(home).toContain('The base did not advance, or was measured back. The robot moved (an action started).');
    expect(home).not.toContain('at its start');
    expect(home).not.toContain('The arm moved');
    expect(stopState(true, false)).toContain('The base advanced and was not measured back. No action started.');
    expect(stopState(null, null)).toContain('Not known whether the base advanced. Not known whether an action started.');
  });

  it("lists exactly the runtime's steps, in its order, and offers no Try again the hold does not list", () => {
    const markup = render(outcomeCard(input()));
    const steps = [...markup.matchAll(/data-step="([a-z_]+)"/g)].map((match) => match[1]);
    expect(steps).toEqual(['open_gripper_by_hand', 'return_arm', 'start']);
    expect(markup).not.toContain('data-control="retry"');
    expect(text(markup)).toContain('Open the gripper by hand and take the block out.');
  });

  it('offers Try again, primary, only when the hold lists retry', () => {
    const held = mission({
      state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision', next_actions: ['retry', 'stop'],
      hold: { target_id: 'cube', attempt: 1, run_id: RUN2, park_run_id: null, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN', observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['retry', 'stop'], budget_left: 1, retry_requested: false },
    });
    const markup = render(outcomeCard(input({ mission: held })));
    expect(markup).toMatch(/data-control="retry"[^>]*data-primary="true"|data-primary="true"[^>]*data-control="retry"/);
    const stopOnly = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision', next_actions: ['stop'], hold: { ...(held.hold as NonNullable<MissionRecord['hold']>), next_actions: ['stop'] } });
    expect(render(outcomeCard(input({ mission: stopOnly })))).not.toContain('data-control="retry"');
  });

  it("draws the failure's own frame, named by run.finished, and no other", () => {
    const withFrame = RUN2_EVENTS.map((event) =>
      event.kind === 'run.finished'
        ? ({ ...event, payload: { outcome: 'failed', exit_status: 5, reason_code: 'align_cube_not_detected', phase: 'ALIGN', failure: { detail: 'ALIGN: the cube is not detected', measurements: [{ name: 'attempt', value: 2, unit: null }], observation_ids: ['obs-descent'] } } } as unknown as ContractEvent)
        : event,
    );
    const markup = render(outcomeCard(input({ runEvents: { [RUN2]: withFrame } })));
    expect(markup).toContain('data-failure-frame="obs-descent"');
    expect(markup).not.toContain('obs-20260927T142354Z-wrist-0001');
  });

  it("says Ended at the last run's finish, labelled as derived", () => {
    const markup = render(outcomeCard(input()));
    expect(markup).toMatch(/data-ended="" data-t="2026-09-27T14:24:40.114Z"/);
    expect(text(markup)).toContain('Ended 14:24:40Z');
    expect(markup).toContain("the time the mission&#x27;s last run finished; the mission record carries no end time");
  });
});

describe('the card with no mission says why there is none, from the read (verifier V6)', () => {
  const none = (missionError: string | null): string => text(render(outcomeCard(input({ mission: null, missionError }))));

  it('says there is no mission yet only when the runtime answered no_mission_recorded', () => {
    expect(none('no_mission_recorded')).toContain('No mission yet in this runtime session.');
  });

  it('says the mission has not been read yet before the first read answers', () => {
    const words = none(null);
    expect(words).toContain('The mission has not been read yet.');
    expect(words).not.toContain('No mission yet');
  });

  it("says the mission has not been read, with the read's code, when the read failed for another reason", () => {
    const words = none('mission_surface_unavailable');
    expect(words).toContain('The mission has not been read: mission_surface_unavailable.');
    expect(words).not.toContain('No mission yet');
  });
});

describe("the run's word comes from the run's own events only (verifier V13)", () => {
  it('gives the run no word while only the finished-run list carries it, never a guess that no stop was asked for', () => {
    const model = outcomeCard(input({ runEvents: {} }));
    expect(model.run?.verdict).toBeNull();
    const markup = render(model);
    expect(markup).toContain('data-run-verdict="none"');
    expect(text(markup)).not.toContain('stopped by the controller');
  });

  it("gives the word once the run's events are read: a stop asked for in them reads stopped on request", () => {
    const asked = [...RUN2_EVENTS.slice(0, -1), ev(3905, 'stop.requested', '2026-09-27T14:24:39.500Z', { origin: 'operator' }), RUN2_EVENTS[RUN2_EVENTS.length - 1] as ContractEvent];
    expect(outcomeCard(input({ runEvents: { [RUN2]: asked } })).run?.verdict?.id).toBe('stopped_on_request');
    expect(outcomeCard(input()).run?.verdict?.id).toBe('stopped_by_controller');
  });
});

describe('the acknowledgement of an ending', () => {
  it('keeps the card expanded until Acknowledge, then collapses it to one line with both times', () => {
    const before = render(outcomeCard(input()));
    expect(before).toContain('data-outcome-card="expanded"');
    expect(before).toContain('data-control="acknowledge"');
    const ack: AckRecord = { key: ackKeyOf(mission()) ?? '', mission_id: 'mission-f65c3cf8e99f', run_id: RUN2, attempt: 1, verdict: 'failed', acknowledged_at: '2026-09-27T14:31:05.000Z' };
    const after = render(outcomeCard(input({ acknowledgements: [ack] })));
    expect(after).toContain('data-outcome-card="collapsed"');
    expect(text(after)).toContain('Mission failed at 14:24:40Z. Acknowledged on this display at 14:31:05Z.');
  });

  it('comes back expanded for a new attempt, whose key differs', () => {
    const ack: AckRecord = { key: ackKeyOf(mission()) ?? '', mission_id: 'mission-f65c3cf8e99f', run_id: RUN2, attempt: 1, verdict: 'failed', acknowledged_at: '2026-09-27T14:31:05.000Z' };
    const second = mission({}, { ...ATTEMPT, attempt: 2, run_id: 'console-20260927T150000Z-mnew' });
    expect(ackKeyOf(second)).not.toBe(ack.key);
    expect(render(outcomeCard(input({ mission: second, acknowledgements: [ack] })))).toContain('data-outcome-card="expanded"');
  });

  it('item 13: two missions with one id and different starts have different keys', () => {
    const first = mission();
    const again = mission({ started_wall_utc: '2026-09-27T15:02:00.000Z' });
    expect(ackKeyOf(again)).not.toBe(ackKeyOf(first));
    const ack: AckRecord = { key: ackKeyOf(first) ?? '', mission_id: 'mission-f65c3cf8e99f', run_id: RUN2, attempt: 1, verdict: 'failed', acknowledged_at: '2026-09-27T14:31:05.000Z' };
    expect(render(outcomeCard(input({ mission: again, acknowledgements: [ack] })))).toContain('data-outcome-card="expanded"');
  });

  it('item 13: never collapses on an acknowledgement older than the mission start', () => {
    const ack: AckRecord = { key: ackKeyOf(mission()) ?? '', mission_id: 'mission-f65c3cf8e99f', run_id: RUN2, attempt: 1, verdict: 'failed', acknowledged_at: '2026-09-27T14:10:00.000Z' };
    const card = outcomeCard(input({ acknowledgements: [ack] }));
    expect(card.acknowledged).toBeNull();
    expect(card.document_title).not.toBeNull();
    expect(render(card)).toContain('data-outcome-card="expanded"');
  });
});

describe('the acknowledgement, kept by the store', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:31:05Z') }));
  afterEach(() => vi.useRealTimers());

  it('records the press and keeps the record in storage, never sending it anywhere', async () => {
    const stored: Record<string, string> = {};
    const storage = { getItem: (key: string) => stored[key] ?? null, setItem: (key: string, value: string) => void (stored[key] = value) };
    const source = new SyntheticSource({ scenario: 'far-block-success' });
    const store = new ConsoleStore(source, { storage });
    store.acknowledge({ key: 'k1', mission_id: 'm1', run_id: 'r1', attempt: 1, verdict: 'failed' });
    expect(store.getSnapshot().acknowledgements.map((item) => item.key)).toEqual(['k1']);
    expect(store.getSnapshot().presses.at(-1)).toMatchObject({ control: 'acknowledge', answer: 'taken' });
    expect(JSON.parse(stored[ACKNOWLEDGED_KEY] ?? '[]')).toEqual([{ key: 'k1', mission_id: 'm1', run_id: 'r1', attempt: 1, verdict: 'failed', acknowledged_at: '2026-09-27T14:31:05.000Z' }]);
    const reloaded = new ConsoleStore(source, { storage });
    expect(reloaded.getSnapshot().acknowledgements.map((item) => item.key)).toEqual(['k1']);
  });

  it('keeps the card expanded after a reload when the storage throws', () => {
    const throwing = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
    const store = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }), { storage: throwing });
    const key = ackKeyOf(mission()) ?? '';
    store.acknowledge({ key, mission_id: 'mission-f65c3cf8e99f', run_id: RUN2, attempt: 1, verdict: 'failed' });
    const reloaded = new ConsoleStore(new SyntheticSource({ scenario: 'far-block-success' }), { storage: throwing });
    expect(reloaded.getSnapshot().acknowledgements).toEqual([]);
    expect(render(outcomeCard(input({ acknowledgements: reloaded.getSnapshot().acknowledgements })))).toContain('data-outcome-card="expanded"');
  });
});

describe('the placement on the Complete card (spec 4.4)', () => {
  const PLACED_EVENTS: ContractEvent[] = [
    ev(10, 'run.started', '2026-09-27T14:21:46.510Z', { command_type: 'start_cycle' }),
    ev(90, 'diagnostic.text', '2026-09-27T14:23:10.000Z', { text: '[STATE] PLACED {"value": true, "cube_pad_m": [-0.0158, -0.1], "centre_to_edge_mm": 47.5}' }),
    ev(95, 'run.finished', '2026-09-27T14:23:12.000Z', { outcome: 'placed_new', exit_status: 0, reason_code: null, phase: 'DONE', failure: null }),
  ];
  const complete = (): MissionRecord =>
    mission(
      { state: 'COMPLETE', detail: null, next_actions: [], targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'COMPLETE', runs: [RUN2], attempts: [], retries: [], park_runs: [] }] },
    );

  it('states where the run placed the block, in the scene map\'s own line', () => {
    const model = outcomeCard(input({ mission: complete(), drawnRunEvents: { [RUN2]: PLACED_EVENTS } }));
    expect(model.placement).toBe('Placed at x -15.8 mm, y -100.0 mm, its centre 47.5 mm from the nearest edge.');
    const markup = render(model);
    expect(markup).toContain('data-outcome-placement=""');
    expect(text(markup)).toContain('Placed at x -15.8 mm, y -100.0 mm, its centre 47.5 mm from the nearest edge.');
  });

  it('says nothing of a placement when the run\'s records carry none', () => {
    const model = outcomeCard(input());
    // Another run's placement is never this run's.
    expect(outcomeCard(input({ mission: complete(), drawnRunEvents: { [RUN1]: PLACED_EVENTS } })).placement).toBeNull();
    expect(model.placement).toBeNull();
    expect(render(model)).not.toContain('data-outcome-placement');
    expect(text(render(outcomeCard(input({ mission: complete(), drawnRunEvents: { [RUN2]: PLACED_EVENTS.filter((event) => event.kind !== 'diagnostic.text') } }))))).not.toContain('Placed at');
  });
});

describe('the card\'s rule (browser D1)', () => {
  it('draws a 4 px left rule in the verdict colour and a 1 px rule on the other sides, in longhands only', () => {
    const style = /<section[^>]*data-region="mission"[^>]*style="([^"]*)"/.exec(render(outcomeCard(input())))?.[1] ?? '';
    const rules = Object.fromEntries(style.split(';').filter((item) => item.includes(':')).map((item) => [item.slice(0, item.indexOf(':')), item.slice(item.indexOf(':') + 1)]));
    // No shorthand beside the left longhands: React warns when it updates one while the other is set.
    expect(Object.keys(rules).filter((name) => name === 'border' || name === 'border-left' || name === 'border-width' || name === 'border-color' || name === 'border-style')).toEqual([]);
    expect([rules['border-left-width'], rules['border-left-style'], rules['border-left-color']]).toEqual(['4px', 'solid', TONE_COLOUR.bad]);
    for (const side of ['top', 'right', 'bottom']) {
      expect([rules[`border-${side}-width`], rules[`border-${side}-style`], rules[`border-${side}-color`]]).toEqual(['1px', 'solid', wash(TONE_COLOUR.bad, 0.25)]);
    }
  });
});
