// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MAIN SCREEN SHOWS WHAT IS CURRENT; HISTORY HOLDS THE REST, DATED (docs/autonomous-demo/console-redesign-2026-09-27.md,
// section 12; operator ruling 2026-09-28).
//
// The live case of 2026-09-28: the runtime was restarted today (session rt-20260928T172310Z-46d1dc34) and still serves
// yesterday's FAILED mission as its mission, its last outcome and its finished runs. The main screen drew "Mission failed"
// as if it had just happened. Here that runtime is played through a whole store, as a page reads it: the main screen
// carries one neutral line and no failure card, and History carries the mission with its full dates and its session. A
// mission of this session stays on the main screen through its hold and its end, on the watching page and on a reload.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { SyntheticSource } from '../api/synthetic';
import type { Snapshot } from '@kiwi/demo-contracts';
import type { HelloResult, HistoryEntry, HistoryEventsResult, HistoryResult, MissionResult } from '../api/types';
import { parseMission } from '../model/missionRecord';
import { ConsoleStore } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { currentWrist, DemoScreen } from './DemoScreen';
import type { CameraPanelModel } from './cameras';
import { currencyContext } from '../model/currency';
import { outcomeCard } from '../model/outcomeCard';
import { missionCurrency } from '../model/currency';
import type { ScreenOpening } from './DemoScreen';
import { element, settle, textOf } from './demoScenarioPlay';
import { Actions } from '../panels/Actions';
import { currentHoldingMission, deriveHolding } from '../panels/commandGuards';

const SESSION = 'rt-20260928T172310Z-46d1dc34';
const OLD_MISSION = 'mission-f65c3cf8e99f';
const OLD_RUN = 'console-20260927T142146Z-m6ed2dd9';
const NEW_MISSION = 'mission-d2f1b2ba17c5';
const NEW_RUN = 'console-20260928T173714Z-mf0eb1d6';

function ev(runId: string, seq: number, kind: string, t: string, payload: Record<string, unknown>, reason: string | null = null): ContractEvent {
  return {
    schema_version: '1', seq, run_id: runId, action_id: null, observation_id: null,
    producer: { name: 'demo-runtime', version: '0.1.0', artifact_digest: null, contract_version: '1' },
    source_session: 'rt-20260927T141418Z-89540562', t_mono_ns: seq * 1000, clock_domain: 'laptop_monotonic', t_wall_utc: t,
    reason_code: reason, provenance: null, kind, payload,
  } as unknown as ContractEvent;
}

const OLD_EVENTS: ContractEvent[] = [
  ev(OLD_RUN, 3700, 'run.started', '2026-09-27T14:21:46.200Z', { command_id: 'm6ed2dd9', command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: null }),
  ev(OLD_RUN, 3701, 'state.transition', '2026-09-27T14:21:47.000Z', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }),
  ev(OLD_RUN, 3890, 'state.transition', '2026-09-27T14:24:26.000Z', { machine: 'cycle', from_state: 'ALIGN', to_state: 'GRASP_CLOSE' }),
  ev(OLD_RUN, 3906, 'run.finished', '2026-09-27T14:24:40.112Z', {
    outcome: 'stopped', exit_status: 4, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE',
    failure: { detail: 'base velocity not confirmed zero before an arm segment: stopping all motion', measurements: [], observation_ids: [] },
  }, 'base_not_zero_before_segment'),
];

const NEW_EVENTS: ContractEvent[] = [
  ev(NEW_RUN, 4030, 'run.started', '2026-09-28T17:37:14.607Z', { command_id: 'mf0eb1d6', command_type: 'start_cycle', data_mode: 'live', plan_only: false, holding: null }),
  ev(NEW_RUN, 4037, 'state.transition', '2026-09-28T17:37:15.000Z', { machine: 'cycle', from_state: null, to_state: 'SEARCH' }),
  ev(NEW_RUN, 4052, 'run.finished', '2026-09-28T17:37:34.381Z', {
    outcome: 'failed', exit_status: 5, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0, reason_code: 'mission_binding_no_candidate', phase: 'SEARCH',
    failure: { detail: 'BINDING: target cube needs one cube that is not placed and this SEARCH measured 0; nothing was picked up', measurements: [{ name: 'matching_candidates', value: 0, unit: null }], observation_ids: [] },
  }, 'mission_binding_no_candidate'),
];

function historyRow(runId: string, started: string, finished: string, outcome: string): HistoryEntry {
  return {
    run_id: runId, command_id: runId.slice(-8), command_type: 'start_cycle', started_wall_utc: started, finished_wall_utc: finished,
    outcome, exit_status: outcome === 'failed' ? 5 : 4, evidence_complete: true, evidence_gaps: 0, artifact_digest: null, adopted: false, data_mode: 'live',
  } as unknown as HistoryEntry;
}

const OLD_ROW = historyRow(OLD_RUN, '2026-09-27T14:21:46.200Z', '2026-09-27T14:24:40.114Z', 'stopped');
const NEW_ROW = historyRow(NEW_RUN, '2026-09-28T17:37:14.607Z', '2026-09-28T17:37:34.381Z', 'failed');

const SLOT = { read: 'ok', name: 'mobile_approach', capability_active: false, generation: '1', capsule: '96'.repeat(32), epoch: '2', instance: '6003861253238292481', session_uuid: '01a0e90a8c9979b9aa573b405d131ca5', state: 'ACTIVE' };

function oldMission(state: 'FAILED' | 'STOPPED' | 'WAITING_FOR_CAPABILITY'): Record<string, unknown> {
  const waiting = state === 'WAITING_FOR_CAPABILITY';
  const attempt = {
    attempt: 1, run_id: OLD_RUN, park_run_id: null, outcome_class: waiting ? 'operator_needed' : 'stopped', reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE',
    measurements: [], observation_ids: [], stop_state: { holding: true, base_ahead: false, moved: true }, next_actions: waiting ? ['stop'] : ['open_gripper_by_hand', 'return_arm', 'start'],
    budget_left: 1, t_wall_utc: '2026-09-27T14:24:40.265Z',
  };
  return {
    mission_record_version: '1', mission_id: OLD_MISSION, state, data_mode: 'live', performs_motion: true, execution_label: 'proven-single-cube-controller',
    started_wall_utc: '2026-09-27T14:16:38.010Z', selected_target: 'cube', waiting_reason: waiting ? 'operator_decision' : null, detail: null,
    stop_requested: false, stop_requested_wall_utc: null, records_after_end: 0,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: waiting ? 'WAITING_FOR_CAPABILITY' : state, runs: [OLD_RUN], attempts: [attempt], retries: [], park_runs: [] }],
    activations: [{
      operation_uuid: 'aa'.repeat(16), target_id: 'cube', candidate_key: 'mobile-demo-swapped', payload_digest: 'sha256:' + '4d'.repeat(32), activation_state: 'OBSERVED', outcome: 'ACCEPTED',
      transaction_phase: 'COMPLETED', transaction_uuid: 'cb'.repeat(16), detail: 'the transaction completed', slot_before: SLOT,
      slot_after: { ...SLOT, capability_active: true, generation: '2', capsule: '2f'.repeat(32), epoch: '3' }, stopped_during_activation: false, t_wall_utc: '2026-09-27T14:21:45.496Z',
    }],
    hold: waiting ? { ...attempt, target_id: 'cube', detail: 'base velocity not confirmed zero', retry_requested: false } : null,
    next_actions: waiting ? ['stop'] : ['open_gripper_by_hand', 'return_arm', 'start'],
  };
}

function newMission(state: 'WAITING_FOR_CAPABILITY' | 'FAILED'): Record<string, unknown> {
  const waiting = state === 'WAITING_FOR_CAPABILITY';
  const attempt = {
    attempt: 1, run_id: NEW_RUN, park_run_id: null, outcome_class: 'operator_needed', reason_code: 'mission_binding_no_candidate', phase: 'SEARCH',
    measurements: [{ name: 'matching_candidates', value: 0, unit: null }], observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: false },
    next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-09-28T17:37:34.515Z',
  };
  return {
    mission_record_version: '1', mission_id: NEW_MISSION, state, data_mode: 'live', performs_motion: true, execution_label: 'proven-single-cube-controller',
    started_wall_utc: '2026-09-28T17:36:18.502Z', selected_target: 'cube', waiting_reason: waiting ? 'operator_decision' : null, detail: null,
    stop_requested: false, stop_requested_wall_utc: null, records_after_end: 0,
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state, runs: [NEW_RUN], attempts: [attempt], retries: [], park_runs: [] }],
    activations: [],
    hold: waiting ? { ...attempt, target_id: 'cube', detail: 'BINDING: target cube needs one cube that is not placed', retry_requested: false } : null,
    next_actions: waiting ? ['stop'] : ['start'],
  };
}

/** A runtime restarted today: its own session, and whatever mission, finished runs and last outcome its record holds. */
class RestartedRuntime extends SyntheticSource {
  mission_answer: Record<string, unknown> = oldMission('FAILED');
  rows: HistoryEntry[] = [OLD_ROW];
  events: Record<string, ContractEvent[]> = { [OLD_RUN]: OLD_EVENTS };

  override async hello(): Promise<HelloResult> {
    return { ...(await super.hello()), runtime_session: SESSION };
  }

  override async snapshot(): Promise<Snapshot> {
    const base = await super.snapshot();
    const last = this.rows[this.rows.length - 1];
    return {
      ...base,
      runtime_session: SESSION,
      last_outcome:
        last === undefined
          ? base.last_outcome
          : ({ run_id: last.run_id, finished_seq: 3906, finished_wall_utc: last.finished_wall_utc, result: { outcome: last.outcome, exit_status: 4, predicates: { object_secured: null, placed: null, clearance_target_met: null, base_returned: null }, automatic_retries: 0, task_control_interventions: 0, evidence_complete: true, evidence_gaps: 0 } } as unknown as Snapshot['last_outcome']),
    };
  }

  override async mission(): Promise<MissionResult> {
    const parsed = parseMission(this.mission_answer);
    return parsed === null ? { ok: false, reason_code: 'unreadable' } : { ok: true, mission: parsed };
  }

  override async history(): Promise<HistoryResult> {
    return { ok: true, entries: this.rows };
  }

  override async historyEvents(runId: string): Promise<HistoryEventsResult> {
    const events = this.events[runId];
    return events === undefined ? { ok: false, reason_code: 'unknown_run' } : { ok: true, run_id: runId, events, origins: [] };
  }
}

function render(store: ConsoleStore, opening?: ScreenOpening): string {
  return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen, { opening })));
}

async function open(source: RestartedRuntime): Promise<ConsoleStore> {
  const store = new ConsoleStore(source, { storage: null });
  await store.start();
  await settle(store);
  await store.refreshMission();
  await vi.advanceTimersByTimeAsync(0);
  await settle(store);
  return store;
}

function region(markup: string, name: string): string {
  return element(markup, `data-region="${name}"`) ?? '';
}

describe('a runtime restarted today that still serves yesterday\'s FAILED mission', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T18:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('the main screen draws no failure card: one neutral dated line that points at History', async () => {
    const store = await open(new RestartedRuntime());
    const markup = render(store);
    const mission = region(markup, 'mission');
    expect(mission).toContain('data-outcome-card="past"');
    expect(mission).not.toContain('data-verdict="failed"');
    expect(textOf(mission)).toContain('Last mission: failed, ended 2026-09-27 14:24:40Z (in History)');
    expect(textOf(mission)).not.toContain('Mission failed');
    expect(mission).not.toContain('data-cause=');
    expect(mission).not.toContain('data-where=');
    expect(mission).toContain('data-control="open-history"');
    store.dispose();
  });

  it("draws no step list from that mission's end, and keeps Return arm on the main screen as the capability list allows it now", async () => {
    // verify-r1 M2: the ended mission's step list is the runtime's reading of that mission's last stop state, recorded at
    // its end (yesterday), not a reading of today. It is History's; Return arm stays reachable, guarded as every control is.
    const store = await open(new RestartedRuntime());
    const mission = region(render(store), 'mission');
    expect(mission).not.toContain('data-steps=');
    expect(mission).not.toContain('data-step=');
    expect(mission).not.toContain('data-by-hand=');
    expect(textOf(mission)).not.toContain('Open the gripper by hand');
    expect(textOf(mission)).not.toContain('now, from that mission');
    expect(mission).toContain('data-past-return-arm=""');
    expect(mission).toContain('data-control="return_arm"');
    store.dispose();
  });

  it("the top bar's readiness verdict reads no step of that mission: the whole main screen carries none of its by-hand steps", async () => {
    // verify-r2 M1 (both lenses): the readiness verdict read the record's mission whether current or past, and rule 3 turned
    // yesterday's step list into "Steps first: Open the gripper by hand", undated, in the warn tone, at the top of the screen.
    const store = await open(new RestartedRuntime());
    const markup = render(store);
    const main = textOf(markup);
    expect(main).not.toContain('Open the gripper by hand');
    expect(main).not.toContain('Steps first');
    expect(markup).not.toContain('data-verdict="steps_first"');
    // The Return arm control is still on the main screen, from what the runtime serves now (its capability list).
    expect(region(markup, 'mission')).toContain('data-control="return_arm"');
    store.dispose();
  });

  it('no timeline line, Cake card, stage word or scene of that mission is on the main screen', async () => {
    const store = await open(new RestartedRuntime());
    const markup = render(store);
    const keys = [...markup.matchAll(/data-timeline-entry="[a-z]+" data-key="([^"]+)"/g)].map((match) => match[1] ?? '');
    expect(keys.filter((key) => key.includes(OLD_MISSION) || key.includes(OLD_RUN) || key.startsWith('activation:'))).toEqual([]);
    expect(markup).not.toContain('data-activation-card=');
    expect(textOf(region(markup, 'cake'))).toContain('No Cake activation in this runtime session');
    const statuses = [...region(markup, 'stages').matchAll(/data-stage-status="([a-z_]+)"/g)].map((match) => match[1]);
    expect(statuses.every((status) => status === 'not_reached')).toBe(true);
    expect(textOf(markup)).not.toContain(OLD_RUN);
    store.dispose();
  });

  it('History carries the mission, its run and its activation, each with its full date and time and its session', async () => {
    const store = await open(new RestartedRuntime());
    const history = textOf(element(render(store, { drawer: 'history' }), 'data-drawer-overlay=""') ?? '');
    expect(history).toContain('Mission failed');
    expect(history).toContain('Started 2026-09-27 14:16:38Z');
    expect(history).toContain('Ended 2026-09-27 14:24:40Z');
    expect(history).toContain('an earlier runtime session (recorded before rt-20260928T172310Z-46d1dc34 started)');
    expect(history).toContain('2026-09-27 14:21:45Z');
    expect(history).toContain(OLD_RUN);
    expect(history).toContain('finished 2026-09-27 14:24:40Z');
    store.dispose();
  });

  it('an orphaned hold of that mission is NOT moved: it waits for the operator, so it stays on the main screen', async () => {
    const source = new RestartedRuntime();
    source.mission_answer = oldMission('WAITING_FOR_CAPABILITY');
    const store = await open(source);
    const mission = region(render(store), 'mission');
    expect(mission).toContain('data-outcome-card="carried"');
    expect(mission).toContain('data-verdict="held_for_you"');
    expect(mission).toContain('Started 2026-09-27 14:16:38Z');
    store.dispose();
  });
});

describe('a mission started in this runtime session', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T18:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  function thisSession(state: 'WAITING_FOR_CAPABILITY' | 'FAILED'): RestartedRuntime {
    const source = new RestartedRuntime();
    source.mission_answer = newMission(state);
    source.rows = [OLD_ROW, NEW_ROW];
    source.events = { [OLD_RUN]: OLD_EVENTS, [NEW_RUN]: NEW_EVENTS };
    return source;
  }

  it('stays on the main screen through its hold and its end, on the watching page', async () => {
    const source = thisSession('WAITING_FOR_CAPABILITY');
    const store = await open(source);
    let mission = region(render(store), 'mission');
    expect(mission).toContain('data-outcome-card="carried"');
    source.mission_answer = newMission('FAILED');
    await store.refreshMission();
    await vi.advanceTimersByTimeAsync(0);
    await settle(store);
    mission = region(render(store), 'mission');
    expect(mission).toContain('data-outcome-card="expanded"');
    expect(mission).toContain('data-verdict="failed"');
    store.dispose();
  });

  it('a reload draws the same mission card as the page that watched it end', async () => {
    const watching = thisSession('WAITING_FOR_CAPABILITY');
    const watcher = await open(watching);
    watching.mission_answer = newMission('FAILED');
    await watcher.refreshMission();
    await vi.advanceTimersByTimeAsync(0);
    await settle(watcher);
    const reloaded = await open(thisSession('FAILED'));
    expect(textOf(region(render(reloaded), 'mission'))).toBe(textOf(region(render(watcher), 'mission')));
    expect(region(render(reloaded), 'mission')).toContain('data-outcome-card="expanded"');
    watcher.dispose();
    reloaded.dispose();
  });

  it('the earlier session\'s finished run is History\'s, never the stages\' run', async () => {
    const store = await open(thisSession('FAILED'));
    const markup = render(store);
    expect(region(markup, 'stages')).not.toContain(OLD_RUN);
    expect(textOf(region(markup, 'mission'))).toContain(NEW_RUN);
    store.dispose();
  });
});

describe('the pieces of the rule the screen reads', () => {
  const context = currencyContext(SESSION, Date.parse('2026-09-28T18:00:00Z'));

  it("a past mission's card has no document title, no acknowledgement key and no verdict: nothing asks the operator about it", () => {
    const mission = parseMission(oldMission('FAILED'));
    if (mission === null) throw new Error('fixture');
    const model = outcomeCard({
      mission, missionError: null, history: [OLD_ROW], runEvents: { [OLD_RUN]: OLD_EVENTS }, activeRunId: null, acknowledgements: [], capability: null, drawnRunEvents: {},
      currency: missionCurrency(mission, [OLD_ROW], context, []), referenceDay: '2026-09-28',
    });
    expect(model.kind).toBe('past');
    expect(model.document_title).toBeNull();
    expect(model.ack_key).toBeNull();
    expect(model.verdict).toBeNull();
    expect(model.cause).toBeNull();
    expect(model.where).toBeNull();
    expect(model.past_line).toBe('Last mission: failed, ended 2026-09-27 14:24:40Z (in History)');
    expect(model.steps).toEqual([]);
    expect(model.primary_step).toBeNull();
  });

  it("the wrist's newest saved frame of an earlier session is not drawn, and the cover dates it", () => {
    const panel = { stillUrl: '/frame/obs-1', still: { observation_id: 'obs-1', t_wall_utc: '2026-09-27T14:24:08.000Z' }, stillRecordedAt: '2026-09-27T14:24:08.000Z', stillKind: 'retained', stillRecordedAgeMs: 1, cover: null } as unknown as CameraPanelModel;
    const shown = currentWrist(panel, context);
    expect(shown.stillUrl).toBeNull();
    expect(shown.cover?.title).toBe('No picture from this camera in this runtime session.');
    expect(shown.cover?.note).toBe('The newest saved frame is from 2026-09-27 14:24:08Z, an earlier runtime session; it is not shown here.');
    const today = { ...panel, stillRecordedAt: '2026-09-28T17:40:00.000Z' } as CameraPanelModel;
    expect(currentWrist(today, context)).toBe(today);
  });
});

describe('a mission of an earlier session, then a new mission started in this one on the same page', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T18:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  // The operator's live sequence (verify-r1 M1): the page opens on yesterday's mission, Start is pressed, and the runtime
  // serves today's mission, held for capability. The store's timeline is append-only and still holds yesterday's lines.
  for (const ended of ['FAILED', 'STOPPED'] as const) {
    it(`the ${ended} mission's lines do not come back to the main timeline once today's mission is served`, async () => {
      const source = new RestartedRuntime();
      source.mission_answer = oldMission(ended);
      const store = await open(source);
      source.mission_answer = newMission('WAITING_FOR_CAPABILITY');
      source.rows = [OLD_ROW, NEW_ROW];
      source.events = { [OLD_RUN]: OLD_EVENTS, [NEW_RUN]: NEW_EVENTS };
      await store.refreshMission();
      await vi.advanceTimersByTimeAsync(0);
      await settle(store);
      const markup = render(store);
      const timeline = region(markup, 'timeline');
      const keys = [...timeline.matchAll(/data-key="([^"]+)"/g)].map((match) => match[1] ?? '');
      expect(keys.some((key) => key.startsWith(`mission:${NEW_MISSION}:`))).toBe(true);
      expect(keys.filter((key) => key.includes(OLD_MISSION) || key.includes(OLD_RUN) || key.startsWith('activation:'))).toEqual([]);
      expect(textOf(timeline)).not.toContain(OLD_MISSION);
      expect(textOf(timeline)).not.toContain('2026-09-27');
      expect(textOf(timeline)).not.toContain('Mission failed');
      expect(textOf(timeline)).not.toContain('Mission stopped');
      expect(region(markup, 'mission')).toContain('data-outcome-card="carried"');
      store.dispose();
    });
  }

  it('History still carries the earlier mission, dated and placed, beside the one the runtime serves now', async () => {
    const source = new RestartedRuntime();
    const store = await open(source);
    source.mission_answer = newMission('WAITING_FOR_CAPABILITY');
    source.rows = [OLD_ROW, NEW_ROW];
    source.events = { [OLD_RUN]: OLD_EVENTS, [NEW_RUN]: NEW_EVENTS };
    await store.refreshMission();
    await vi.advanceTimersByTimeAsync(0);
    await settle(store);
    const history = textOf(element(render(store, { drawer: 'history' }), 'data-drawer-overlay=""') ?? '');
    expect(history).toContain(NEW_MISSION);
    expect(history).toContain(OLD_MISSION);
    expect(history).toContain('Started 2026-09-27 14:16:38Z');
    expect(history).toContain('Ended 2026-09-27 14:24:40Z');
    expect(history).toContain('an earlier runtime session (recorded before rt-20260928T172310Z-46d1dc34 started)');
    store.dispose();
  });
});

describe("Observe's holding argument never rests on a mission that is History's (verify-r3 M3)", () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T18:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  /** The Observe control as the main screen draws it, with the capability list enabling it. */
  function observe(store: ConsoleStore): string {
    const fresh = store.getSnapshot();
    const view = { ...fresh.view, data_mode: 'live' as const, capabilities: [{ id: 'observe', state: 'enabled' as const, reason_code: null, reason: null, dependency: null, planned: false }] };
    return textOf(renderToStaticMarkup(createElement(Actions, { snapshot: { ...fresh, view }, store, only: ['observe'] })));
  }

  it("yesterday's stop state is not the current Observe argument: the operator is asked, and told why, with the date", async () => {
    const store = await open(new RestartedRuntime());
    const words = observe(store);
    expect(words).not.toContain('Sent as: Holding the block');
    expect(words).not.toContain(OLD_RUN);
    expect(words).toContain('Off until you state whether the gripper is holding the cube.');
    expect(words).toContain(
      `The mission record the runtime serves, ${OLD_MISSION}, is History's (its newest record 2026-09-27 14:24:40Z, an earlier runtime session (recorded before ${SESSION} started)), so it does not settle whether the gripper is holding the cube now`,
    );
    // The click reads the same mission as the screen: none.
    expect(currentHoldingMission(store.getSnapshot(), Date.now()).mission).toBeNull();
    expect(deriveHolding(store.getSnapshot().view, currentHoldingMission(store.getSnapshot(), Date.now()).mission).value).toBeNull();
    store.dispose();
  });

  it('a hold of that mission waits for the operator, so it is current and still settles holding', async () => {
    const source = new RestartedRuntime();
    source.mission_answer = oldMission('WAITING_FOR_CAPABILITY');
    const store = await open(source);
    const held = currentHoldingMission(store.getSnapshot(), Date.now());
    expect(held.mission?.mission_id).toBe(OLD_MISSION);
    expect(held.past).toBeNull();
    expect(deriveHolding(store.getSnapshot().view, held.mission)).toEqual({
      value: true,
      basis: `the mission record, attempt 1 of run ${OLD_RUN}, whose stop state records holding true`,
    });
    store.dispose();
  });
});
