// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DEMO DIRECTOR IN THE STORE: armed by this tab's Start, it sends the activation, the stop and the carry from the
// records, each once, and nothing when it is not armed (a reload of another tab, a page opened on a waiting mission).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource } from '../api/types';
import { parseMission } from '../model/missionRecord';
import type { MissionRecord } from '../model/missionRecord';
import type { VlaStatus } from '../model/vla';
import { ConsoleStore } from './store';

const ID = 'mission-7f6e958dd257';
const HOLDING = { attempt: 1, run_id: 'run-2', park_run_id: null, outcome_class: 'operator_needed', reason_code: 'hold_observe_no_pad_frame', phase: 'HOLD_OBSERVE',
  measurements: [], observation_ids: [], stop_state: { base_ahead: false, holding: true, moved: true }, next_actions: ['stop'], budget_left: 1, t_wall_utc: '2026-10-11T15:15:47Z' };

function mission(patch: Record<string, unknown>, attempt: Record<string, unknown> | null = null): MissionRecord {
  const record = parseMission({
    mission_record_version: '1', mission_id: ID, state: 'RUNNING', data_mode: 'live', performs_motion: true, execution_label: null, started_wall_utc: '2026-10-11T15:12:40Z',
    selected_target: 'cube', waiting_reason: null, detail: null, activations: [],
    targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'ACTIVE', runs: ['run-1'], attempts: attempt === null ? [] : [attempt] }],
    ...patch,
  });
  if (record === null) throw new Error('fixture');
  return record;
}

interface World { mission: MissionRecord; vla: VlaStatus; activations: string[]; stops: number; vlaOps: string[] }

function storeOver(world: World): ConsoleStore {
  const base = new SyntheticSource();
  const doors = {
    mission: async () => ({ ok: true as const, mission: world.mission }),
    missionCandidates: async () => ({ ok: true as const, candidates: [{ key: 'mobile-demo-swapped', payload_digest: 'sha256:' + 'a'.repeat(64) }] }),
    activateCandidate: async (key: string) => {
      world.activations.push(key);
      world.mission = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', activations: [{ operation_uuid: 'op-1', candidate_key: key, activation_state: 'SUBMITTED' }] });
      return { kind: 'answered' as const, operation_uuid: 'op-1', activation_state: 'SUBMITTED', outcome: 'PENDING', transaction_phase: null, detail: null };
    },
    stop: async () => {
      world.stops += 1;
      world.mission = mission({ state: 'STOPPED', stop_requested: true }, HOLDING);
      return { ok: true, command_id: 'c1', result: null, requested_mono_ns: null, run_id: null, reason: null, outcome: 'taken' as const, mission: { mission_id: ID, stopping: true, reason_code: null, state: 'WAITING_FOR_CAPABILITY', carried: true } };
    },
    vlaStatus: async () => world.vla,
    vlaCommand: async (op: string) => {
      world.vlaOps.push(op);
      if (op === 'carry') world.vla = { state: 'standby', carry: { result: 'done', mission_id: ID } };
      return { ok: true, answer: { accepted: true } };
    },
  };
  const source = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, doors);
  return new ConsoleStore(source, { storage: null, sessionStorage: null });
}

describe('the demo director in the store', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends nothing for a mission this tab did not start', async () => {
    const world: World = { mission: mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }), vla: { state: 'standby' }, activations: [], stops: 0, vlaOps: [] };
    const store = storeOver(world);
    await store.refreshMission();
    await store.directDemo();
    expect(world.activations).toEqual([]);
    expect(store.getSnapshot().directorStep).toBeNull();
  });

  it('armed: activates the one Plan once, stops once at the hold, carries once, then is finished', async () => {
    const world: World = { mission: mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }), vla: { state: 'standby' }, activations: [], stops: 0, vlaOps: [] };
    const store = storeOver(world);
    store.armDemo(ID);
    await store.refreshMission();
    await store.directDemo();
    expect(world.activations).toEqual(['mobile-demo-swapped']);
    await store.refreshMission();
    await store.directDemo();
    expect(world.activations).toEqual(['mobile-demo-swapped']);
    expect(store.getSnapshot().directorStep?.kind).toBe('wait');
    // the pick ends at the hold, holding the block: the director stops the mission
    world.mission = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision' }, HOLDING);
    await store.refreshMission();
    await store.directDemo();
    expect(world.stops).toBe(1);
    // the stop read the mission as STOPPED and the runner is in standby: the carry goes out once
    await store.refreshVla();
    await store.directDemo();
    expect(world.vlaOps).toEqual(['carry']);
    await store.refreshVla();
    await store.directDemo();
    expect(world.vlaOps).toEqual(['carry']);
    expect(world.stops).toBe(1);
    expect(store.getSnapshot().directorStep).toEqual({ kind: 'finished' });
  });

  it('Stop from the operator disarms it', async () => {
    const world: World = { mission: mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }), vla: { state: 'standby' }, activations: [], stops: 0, vlaOps: [] };
    const store = storeOver(world);
    store.armDemo(ID);
    store.armDemo(null);
    await store.refreshMission();
    await store.directDemo();
    expect(world.activations).toEqual([]);
    expect(store.getSnapshot().demoArmed).toBeNull();
  });
});

// A STOP DURING THE DEMO'S START (independent review 2026-10-12, confirmed high): the first Start moves the arm to the
// mission's start pose before it starts the mission. A Stop in that window must cancel the Start: no mission is
// started, and the director stays disarmed.
interface StartWorld { mission: MissionRecord; vla: VlaStatus; vlaOps: string[]; missionStarts: number; stopsTaken: number; startGate: Promise<void> | null }

function startStore(world: StartWorld): ConsoleStore {
  const base = new SyntheticSource();
  const doors = {
    mission: async () => ({ ok: true as const, mission: world.mission }),
    missionCandidates: async () => ({ ok: true as const, candidates: [] }),
    missionStart: async () => {
      world.missionStarts += 1;
      if (world.startGate !== null) await world.startGate;
      world.mission = mission({ state: 'RUNNING' });
      return { kind: 'started' as const, mission_id: ID };
    },
    // the runtime takes a stop only while a mission runs; with none it answers no_active_run
    stop: async () => {
      const running = world.mission.state === 'RUNNING';
      if (running) {
        world.stopsTaken += 1;
        world.mission = mission({ state: 'STOPPED', stop_requested: true });
      }
      return running
        ? { ok: true, command_id: 'c1', result: null, requested_mono_ns: null, run_id: null, reason: null, outcome: 'taken' as const, mission: { mission_id: ID, stopping: true, reason_code: null, state: 'RUNNING', carried: true } }
        : { ok: false, command_id: 'c2', result: null, requested_mono_ns: null, run_id: null, reason: 'no_active_run', outcome: 'refused' as const, mission: null };
    },
    vlaStatus: async () => world.vla,
    vlaCommand: async (op: string) => {
      world.vlaOps.push(op);
      if (op === 'to_mission_pose') world.vla = { state: 'moving' };
      if (op === 'stop') world.vla = { state: 'standby' };
      return { ok: true, answer: { accepted: true } };
    },
  };
  const source = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, doors);
  return new ConsoleStore(source, { storage: null, sessionStorage: null });
}

const idle = (): StartWorld => ({ mission: mission({ state: 'STOPPED' }), vla: { state: 'standby' }, vlaOps: [], missionStarts: 0, stopsTaken: 0, startGate: null });

describe('a Stop during the demo Start', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('pressed while the arm moves to the mission start, starts no mission and leaves the director disarmed', async () => {
    const world = idle();
    const store = startStore(world);
    await store.refreshVla();
    const pending = store.startDemo();
    await vi.advanceTimersByTimeAsync(0);
    expect(world.vlaOps).toEqual(['to_mission_pose']);
    // the operator's Stop, as the show screen sends it: disarm, then the runner's stop (the arm halts, the runner reads standby)
    store.armDemo(null);
    await store.vlaCommand('stop');
    await vi.advanceTimersByTimeAsync(2000);
    const outcome = await pending;
    expect(world.missionStarts).toBe(0);
    expect(store.getSnapshot().demoArmed).toBeNull();
    expect(outcome.kind).toBe('refused');
  });

  it('pressed while the mission start is in flight, stops the mission that start opened and leaves the director disarmed', async () => {
    const world = idle();
    let release: () => void = () => undefined;
    world.startGate = new Promise<void>((resolve) => { release = resolve; });
    const store = startStore(world);
    await store.refreshVla();
    const pending = store.startDemo();
    await vi.advanceTimersByTimeAsync(0);
    world.vla = { state: 'standby' }; // the pose move ends on its own
    await vi.advanceTimersByTimeAsync(600);
    expect(world.missionStarts).toBe(1);
    store.armDemo(null); // the operator's Stop lands while the start is answered
    release();
    await vi.advanceTimersByTimeAsync(0);
    await pending;
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getSnapshot().demoArmed).toBeNull();
    expect(world.stopsTaken).toBe(1);
  });

  it('the show screen\'s Stop reaches the runner while a demo Start is pending, before the runner was read moving', async () => {
    const world = idle();
    const store = startStore(world);
    await store.refreshVla();
    (store as unknown as { snapshot: { demoStarting: boolean } }).snapshot = { ...store.getSnapshot(), demoStarting: true };
    await store.stopDemo();
    expect(world.vlaOps).toEqual(['stop']);
    expect(store.getSnapshot().demoArmed).toBeNull();
  });
});
