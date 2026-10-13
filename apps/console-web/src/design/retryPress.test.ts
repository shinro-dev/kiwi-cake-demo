// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT ONE PRESS OF TRY AGAIN DOES. It is a motion door: the runtime parks the arm and then starts a fresh attempt. So
// it makes the refusals Start and every panel control make, from the same functions, against a snapshot read again at
// the click, and it sends the mission id of the hold it saw so a press meant for one mission can never retry another.

import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, MissionStartOutcome, UnresolvedCommand } from '../api/types';
import { BASELINE_BACKEND } from '../model/robot';
import type { MissionRecord } from '../model/missionRecord';
import { robotStatus } from '../model/robotStatusFixture';
import { alignLostRun } from '../model/stageFixtures';
import type { ViewState } from '../model/types';
import { retryRefusal, submitRefusal } from '../panels/Actions';
import { robotBlock } from '../panels/commandGuards';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { pressRetry } from './retryPress';

function heldMission(patch: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1', mission_id: 'mission-0001', state: 'WAITING_FOR_CAPABILITY', data_mode: 'synthetic', performs_motion: false,
    execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-1', waiting_reason: 'operator_decision', detail: null,
    targets: [{ target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: false, state: 'WAITING_FOR_CAPABILITY', runs: ['r'] }],
    activations: [],
    hold: {
      target_id: 'block-1', attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN',
      observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['retry', 'stop'], budget_left: 1, retry_requested: false,
    },
    ...patch,
  } as MissionRecord;
}

const UNRESOLVED: UnresolvedCommand = {
  command_id: 'cmd-0001', run_id: 'r', command_type: 'start_cycle', reason_code: 'answer_lost', classification: null,
  block_reason_code: 'owner_unresolved', blocked_commands: ['start_cycle', 'observe', 'return_arm'], evidence: null,
} as unknown as UnresolvedCommand;

function recording(): { calls: (string | null)[]; store: ConsoleStore } {
  const calls: (string | null)[] = [];
  const base = new SyntheticSource();
  const source = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, {
    missionRetry: async (missionId: string | null): Promise<MissionStartOutcome> => {
      calls.push(missionId);
      return { kind: 'started', mission_id: missionId };
    },
  });
  return { calls, store: new ConsoleStore(source) };
}

function showing(store: ConsoleStore, patch: Partial<StoreSnapshot>, view: ViewState = alignLostRun('r')): void {
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), mission: heldMission(), missionError: null, ...patch, view };
}

describe('Try again makes the refusals Start makes, again at the click', () => {
  it('sends one retry naming the hold it saw', async () => {
    const { calls, store } = recording();
    showing(store, {});
    expect(retryRefusal(store.getSnapshot(), Date.now())).toBeNull();
    const receipt = await pressRetry(store);
    expect(receipt.tone).toBe('taken');
    expect(calls).toEqual(['mission-0001']);
  });

  it('refuses while a command whose execution is not known blocks motion', async () => {
    const { calls, store } = recording();
    showing(store, {}, { ...alignLostRun('r'), unresolved: UNRESOLVED });
    expect(retryRefusal(store.getSnapshot(), Date.now())).toContain('owner_unresolved');
    const receipt = await pressRetry(store);
    expect(receipt.tone).toBe('refused');
    expect(receipt.text).toContain('owner_unresolved');
    expect(calls).toEqual([]);
  });

  it('refuses while the mode requires a lease the runtime does not hold', async () => {
    const { calls, store } = recording();
    const lease = { held: false, generation: null, host_session: null, expires_in_ms: null, reason: 'lease_not_granted', required: true, attached: false, renew_failures: 0, run_id: null, adopted: null, last_reply_age_ms: null };
    showing(store, {}, { ...alignLostRun('r'), lease } as ViewState);
    expect(retryRefusal(store.getSnapshot(), Date.now())).toContain('lease');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });

  it('refuses on an answer about the robot that stopped being current', async () => {
    const { calls, store } = recording();
    const stale = {
      ...alignLostRun('r'),
      data_mode: 'live',
      execution: { backend: BASELINE_BACKEND, ownership_basis: 'runtime_single_run_slot', robot_side_enforcement: false, detail: 'd' },
      capabilities: [{ id: 'start_cycle', state: 'enabled', reason: null, reason_code: null }],
      snapshot_at_ms: Date.now() - 120_000,
    } as unknown as ViewState;
    showing(store, {}, stale);
    // The robot's answer is not current because the snapshot carrying it is 120 s old: the runtime has not answered, and
    // that is the reason every command control gives first (verdict 2026-09-27 must_fix 3). The robot's own words stand.
    expect(retryRefusal(store.getSnapshot(), Date.now())).toMatch(/^the runtime has not answered since its last snapshot reached this page at \d{2}:\d{2}:\d{2}Z$/);
    expect(robotBlock(stale, Date.now())).toContain('not read here as a statement about the robot now');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });

  it('refuses while the robot reports its motion held, in the permission own words', async () => {
    // During a hold the runtime masks the motion block with mission_running in its capability list, so the retry reads
    // the robot's motion permission itself, the same reading the motion-hold banner draws.
    const { calls, store } = recording();
    showing(store, { robot: robotStatus(), robotError: null, robotReadAtMs: Date.now() });
    const refusal = retryRefusal(store.getSnapshot(), Date.now());
    expect(refusal).toContain('robot motion is held');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });

  it('refuses when the last mission read failed, whatever the kept record says', async () => {
    const { calls, store } = recording();
    showing(store, { missionError: 'the runtime did not answer' });
    expect(retryRefusal(store.getSnapshot(), Date.now())).toContain('the runtime did not answer');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });

  it('refuses when the hold is gone at the click', async () => {
    const { calls, store } = recording();
    showing(store, { mission: heldMission({ state: 'RUNNING', waiting_reason: null }) });
    const receipt = await pressRetry(store);
    expect(receipt.tone).toBe('refused');
    expect(receipt.text).toContain('not held for a decision');
    expect(calls).toEqual([]);
  });

  it('refuses a hold whose stop was asked for', async () => {
    const { calls, store } = recording();
    showing(store, { mission: heldMission({ stop_requested: true }) });
    expect(retryRefusal(store.getSnapshot(), Date.now())).toContain('stopping');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });

  it('sends nothing when the hold the runtime recorded offers only Stop, or when no hold record was served', async () => {
    const stopOnly = recording();
    const base = heldMission();
    showing(stopOnly.store, { mission: heldMission({ hold: { ...(base.hold as NonNullable<MissionRecord['hold']>), next_actions: ['stop'] } }) });
    expect(retryRefusal(stopOnly.store.getSnapshot(), Date.now())).toBe('the runtime offers only Stop for this hold');
    expect((await pressRetry(stopOnly.store)).tone).toBe('refused');
    expect(stopOnly.calls).toEqual([]);
    const unserved = recording();
    showing(unserved.store, { mission: heldMission({ hold: null }) });
    expect(retryRefusal(unserved.store.getSnapshot(), Date.now())).toContain('served no record of the held attempt');
    expect((await pressRetry(unserved.store)).tone).toBe('refused');
    expect(unserved.calls).toEqual([]);
  });

  it('refuses on a recorded run and on a tab the server does not let command', async () => {
    const recorded = recording();
    // A replay open on the screen: the hold beside it is live and would otherwise admit the retry.
    showing(recorded.store, { replay: { run_id: 'r' } as unknown as StoreSnapshot['replay'] });
    expect(retryRefusal(recorded.store.getSnapshot(), Date.now())).toContain('a recorded run is on the screen');
    expect((await pressRetry(recorded.store)).tone).toBe('refused');
    expect(recorded.calls).toEqual([]);

    const { calls, store } = recording();
    showing(store, {}, { ...alignLostRun('r'), commands_enabled: false });
    expect(retryRefusal(store.getSnapshot(), Date.now())).toContain('does not allow this tab to command');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });
});

describe('the hold the runtime serves decides what a press other than Stop may send', () => {
  it('sends no Return arm beside a hold that does not list it, whatever the capability list says', () => {
    const { store } = recording();
    const enabled = [{ id: 'return_arm', state: 'enabled', reason_code: null, reason: null, dependency: null, planned: false }];
    showing(store, {}, { ...alignLostRun('r'), capabilities: enabled } as ViewState);
    expect(submitRefusal(store.getSnapshot(), 'return_arm')).toContain('not Return arm');
    showing(store, { mission: heldMission({ state: 'RUNNING', waiting_reason: null, hold: null }) }, { ...alignLostRun('r'), capabilities: enabled } as ViewState);
    expect(submitRefusal(store.getSnapshot(), 'return_arm')).toBeNull();
  });

  it('sends no second retry once one is written for the held attempt', async () => {
    const { calls, store } = recording();
    const hold = heldMission().hold;
    showing(store, { mission: heldMission({ hold: hold === undefined || hold === null ? null : { ...hold, retry_requested: true } }) });
    expect(retryRefusal(store.getSnapshot(), Date.now())).toContain('already asked for');
    expect((await pressRetry(store)).tone).toBe('refused');
    expect(calls).toEqual([]);
  });

  it('says the mission was stopped once a Stop ended the hold, even though the runtime keeps the waiting reason', () => {
    const { store } = recording();
    showing(store, { mission: heldMission({ state: 'STOPPED', hold: null, stop_requested: true, next_actions: ['return_arm', 'start'] }) });
    const reason = retryRefusal(store.getSnapshot(), Date.now());
    expect(reason).toContain('was stopped, so there is no attempt to retry');
    expect(reason).not.toMatch(/held for a decision|is stopping/);
  });
});
