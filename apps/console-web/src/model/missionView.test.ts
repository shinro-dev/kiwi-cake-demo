// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE SCREEN MAY SAY ABOUT A MISSION THAT CARRIES MORE THAN ONE TARGET.
//
// The single-cube console could answer every question from the fold of one run. A mission cannot: it has a target the
// fold does not name, a held state that is not a failure, and a count that is a read of the record rather than a
// verdict this tab reached. These tests pin the four answers that could each be wrong in a way nobody would notice on
// a screen: the active target, the waiting sentence, the placed count and the mission clock.

import { describe, expect, it } from 'vitest';
import {
  activationOffered,
  activationOffReason,
  activationsForTarget,
  activeTarget,
  holdBlock,
  holdOffer,
  missionClock,
  missionIsRunning,
  missionPlaced,
  missionStartOff,
  missionStateWords,
  missionWaiting,
  missionExecution,
  targetOfRun,
  targetStateWords,
} from './missionView';
import { parseCandidates, parseMission, parseTarget } from './missionRecord';
import type { MissionRecord } from './missionRecord';
import { targetCapabilityWords, targetRequiresCapability } from './missionView';

function mission(extra: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1',
    mission_id: 'mission-0001',
    state: 'RUNNING',
    data_mode: 'live',
    performs_motion: true,
    execution_label: 'physical',
    started_wall_utc: '2026-09-23T10:00:00.000Z',
    selected_target: 'cube-a',
    waiting_reason: null,
    detail: null,
    targets: [
      { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'ACTIVE', runs: ['run-a1'] },
      { target_id: 'cube-b', label: 'Cube B', reachable: false, requires_capability: true, state: 'PENDING', runs: [] },
    ],
    activations: [],
    ...extra,
  };
}

describe('which target the mission is on', () => {
  it('takes the mission own selected target first', () => {
    expect(activeTarget(mission())?.target_id).toBe('cube-a');
    expect(activeTarget(mission({ selected_target: 'cube-b' }))?.target_id).toBe('cube-b');
  });

  it('falls through a selected id that names no target rather than inventing one', () => {
    const odd = mission({
      selected_target: 'cube-z',
      targets: [
        { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: [] },
        { target_id: 'cube-b', label: 'Cube B', reachable: true, requires_capability: null, state: 'WAITING_FOR_CAPABILITY', runs: [] },
      ],
    });
    // Not cube-z, which does not exist, and not the first row either: the one whose own state says it is the one.
    expect(activeTarget(odd)?.target_id).toBe('cube-b');
  });

  it('attributes a run to the target the mission recorded it against, and to no other', () => {
    const record = mission();
    expect(targetOfRun(record, 'run-a1')?.target_id).toBe('cube-a');
    // A run the mission never recorded belongs to nobody here. It is never attached to the nearest target.
    expect(targetOfRun(record, 'run-b1')).toBe(null);
    expect(targetOfRun(record, null)).toBe(null);
    expect(targetOfRun(null, 'run-a1')).toBe(null);
  });
});

describe('a mission that is held', () => {
  const held = mission({
    state: 'WAITING_FOR_CAPABILITY',
    selected_target: 'cube-b',
    waiting_reason: 'capability_unavailable',
    targets: [
      { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: ['run-a1'] },
      {
        target_id: 'cube-b',
        label: 'Cube B',
        reachable: false,
        requires_capability: true,
        state: 'WAITING_FOR_CAPABILITY',
        runs: [],
      },
    ],
  });

  it('says why, and which target is waiting, and names the capability the ROBOT reports', () => {
    // The record says WHETHER (a boolean); the robot's configured capability says WHICH.
    const waiting = missionWaiting(held, 'mobile_approach');
    expect(waiting).not.toBe(null);
    expect(waiting?.target?.target_id).toBe('cube-b');
    expect(waiting?.sentence).toContain('Cube B');
    expect(waiting?.sentence).toContain('mobile_approach');
    expect(waiting?.reasonCode).toBe('capability_unavailable');
    expect(waiting?.capability).toBe('mobile_approach');
  });

  it('says a required capability whose name was not read is unnamed, and never that none is required', () => {
    const waiting = missionWaiting(held);
    expect(waiting?.capability).toContain('has not reported');
    expect(waiting?.sentence).not.toContain('requires no capability');
  });

  it('reads as held and recoverable, never as a crash, a hang or a failure', () => {
    const waiting = missionWaiting(held);
    const words = `${waiting?.headline ?? ''} ${waiting?.sentence ?? ''} ${waiting?.recovery ?? ''}`.toLowerCase();
    expect(waiting?.headline.toLowerCase()).toContain('held');
    expect(words).toContain('nothing has failed');
    for (const forbidden of ['crash', 'hung', 'hang', 'error', 'fault', 'aborted']) {
      expect(words).not.toContain(forbidden);
    }
  });

  it('reads a hold for the operator decision as that, never as a capability wait', () => {
    const decision = mission({ ...held, waiting_reason: 'operator_decision' });
    const waiting = missionWaiting(decision, 'mobile_approach');
    expect(waiting?.kind).toBe('decision');
    expect(waiting?.capability).toBeNull();
    const words = `${waiting?.headline ?? ''} ${waiting?.sentence ?? ''} ${waiting?.recovery ?? ''}`;
    for (const claim of ['waiting for a capability', 'Nothing has failed', 'Cake panel', 'mobile_approach']) expect(words).not.toContain(claim);
    expect(activationOffered(decision)).toBe(false);
    expect(activationOffReason(decision)).toContain('not for a capability');
    const off = missionStartOff({ mission: decision, missionError: null, missionErrorDetail: null, runActive: false }) ?? '';
    expect(off).toContain('held for your decision');
    expect(off).not.toContain('capability');
    expect(off).not.toContain('Cake panel');
  });

  it('claims a capability wait only for the runtime reason capability_unavailable', () => {
    expect(missionWaiting(held)?.kind).toBe('capability');
    for (const reason of [null, 'some_newer_reason']) {
      const other = mission({ ...held, waiting_reason: reason });
      const waiting = missionWaiting(other, 'mobile_approach');
      expect(waiting?.kind).toBe('other');
      expect(`${waiting?.headline ?? ''} ${waiting?.sentence ?? ''} ${waiting?.recovery ?? ''}`).not.toContain('capability');
      expect(activationOffered(other)).toBe(false);
    }
  });

  it('quotes a reason code it has no words for rather than inventing one', () => {
    const odd = missionWaiting(mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'some_newer_reason' }));
    expect(odd?.sentence).toContain('some_newer_reason');
  });

  it('is the only state in which the prepared activation is offered at all', () => {
    expect(activationOffered(held)).toBe(true);
    for (const state of ['RUNNING', 'COMPLETE', 'STOPPED', 'FAILED']) {
      expect(activationOffered(mission({ state }))).toBe(false);
    }
    expect(activationOffered(null)).toBe(false);
  });

  it('is not offered once the held mission has been asked to stop, and says why', () => {
    // The runtime answers any activation for a mission whose stop was requested with mission_stopping.
    const stopping = { ...held, stop_requested: true };
    expect(activationOffered(stopping)).toBe(false);
    expect(activationOffReason(stopping)).toContain('is stopping');
    expect(activationOffReason(stopping)).toContain('mission_stopping');
    expect(activationOffReason(held)).toBeNull();
    expect(activationOffReason(mission({ state: 'RUNNING' }))).toContain('not waiting for a capability');
  });

  it('is still a mission that is going: held is waiting, not finished', () => {
    expect(missionIsRunning(held)).toBe(true);
    expect(missionIsRunning(mission({ state: 'COMPLETE' }))).toBe(false);
  });
});

describe('the placed count', () => {
  it('counts the targets the MISSION records as placed, out of the targets it carries', () => {
    const one = mission({
      targets: [
        { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: [] },
        { target_id: 'cube-b', label: 'Cube B', reachable: true, requires_capability: null, state: 'ACTIVE', runs: [] },
      ],
    });
    expect(missionPlaced(one).words).toBe('1 of 2 cubes placed');
    expect(missionPlaced(one).placed).toBe(1);
    expect(missionPlaced(one).total).toBe(2);
  });

  it('never says placed for a rehearsal: a fixture result is what the rehearsal recorded', () => {
    const targets = [
      { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: false, state: 'PLACED', runs: [] },
      { target_id: 'cube-b', label: 'Cube B', reachable: true, requires_capability: true, state: 'PLACED', runs: [] },
    ];
    // A SIMULATION runtime, and a live runtime whose execution adapter says it performs no motion: both rehearsals.
    for (const extra of [
      { data_mode: 'synthetic', performs_motion: false },
      { data_mode: 'live', performs_motion: false },
      { data_mode: 'synthetic', performs_motion: null },
    ]) {
      const rehearsal = mission({ ...extra, state: 'COMPLETE', targets });
      expect(missionExecution(rehearsal)).toBe('rehearsal');
      expect(missionPlaced(rehearsal).words).not.toContain('placed');
      expect(missionPlaced(rehearsal).words).toContain('Rehearsal');
      expect(missionPlaced(rehearsal).physical).toBe(false);
      expect(targetStateWords('PLACED', rehearsal)).not.toContain('placed');
      expect(targetStateWords('PLACED', rehearsal)).toContain('rehearsal');
    }
    // A live record that does not say whether it moved is not promoted to physical either.
    const unstated = mission({ data_mode: 'live', performs_motion: null, state: 'COMPLETE', targets });
    expect(missionExecution(unstated)).toBe('unstated');
    expect(missionPlaced(unstated).words).not.toContain('placed');
    // Only live AND performs_motion true is a physical placement.
    const physical = mission({ data_mode: 'live', performs_motion: true, state: 'COMPLETE', targets });
    expect(missionPlaced(physical).words).toBe('2 of 2 cubes placed');
    expect(targetStateWords('PLACED', physical)).toBe('placed on the pad');
  });

  it('counts one target in the singular: the one-block demo reads "of 1 cube placed", never "cubes"', () => {
    const target = (state: string) => [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: null, state, runs: [] }];
    const physical = mission({ data_mode: 'live', performs_motion: true, state: 'RUNNING', targets: target('ACTIVE') });
    expect(missionPlaced(physical).words).toBe('0 of 1 cube placed');
    expect(missionPlaced(physical).short).toBe('0 of 1 cube placed');
    const done = mission({ data_mode: 'live', performs_motion: true, state: 'COMPLETE', targets: target('PLACED') });
    expect(missionPlaced(done).words).toBe('1 of 1 cube placed');
    // The rehearsal and the unstated record count one target in the singular as well.
    const rehearsal = mission({ data_mode: 'synthetic', performs_motion: false, state: 'COMPLETE', targets: target('PLACED') });
    expect(missionPlaced(rehearsal).words).toBe('Rehearsal: 1 of 1 target recorded done, no physical placement');
    expect(missionPlaced(rehearsal).short).toBe('Rehearsal: 1 of 1 done');
    const unstated = mission({ data_mode: 'live', performs_motion: null, state: 'COMPLETE', targets: target('PLACED') });
    expect(missionPlaced(unstated).words).toBe('1 of 1 target recorded done; the record does not say the robot moved');
    // Two targets stay plural.
    expect(missionPlaced(mission({ data_mode: 'synthetic', performs_motion: false, targets: [...target('PLACED'), ...target('ACTIVE')] })).words).toBe(
      'Rehearsal: 1 of 2 targets recorded done, no physical placement',
    );
  });

  it('says a mission with no target carries none rather than printing 0 of 0', () => {
    expect(missionPlaced(mission({ targets: [] })).words).toBe('this mission carries no target');
  });
});

describe('the mission clock', () => {
  it('measures from the mission record own start, and says the two clocks are different machines', () => {
    const started = Date.parse('2026-09-23T10:00:00.000Z');
    const clock = missionClock(mission(), started + 65_000);
    expect(clock.words).toBe('01:05');
    expect(clock.running).toBe(true);
    expect(clock.meaning).toContain('different machines');
  });

  it('states time since it began for a mission that has stopped, and never calls it a duration', () => {
    const started = Date.parse('2026-09-23T10:00:00.000Z');
    const clock = missionClock(mission({ state: 'STOPPED' }), started + 5000);
    expect(clock.running).toBe(false);
    expect(clock.meaning).toContain('not its duration');
  });

  it('has no words at all when the record carries no readable start, and never 00:00', () => {
    expect(missionClock(mission({ started_wall_utc: null }), 1).words).toBe(null);
    expect(missionClock(mission({ started_wall_utc: 'not a time' }), 1).words).toBe(null);
  });
});

describe('a target whose mode the robot decides by measurement (requires_capability "auto")', () => {
  const row = (extra: Record<string, unknown>) => ({ target_id: 'cube', label: 'the block', reachable: true, state: 'PENDING', runs: [], ...extra });

  it('reads the third value as itself, and any other word as not recorded', () => {
    expect(parseTarget(row({ requires_capability: 'auto' }))?.requires_capability).toBe('auto');
    expect(parseTarget(row({ requires_capability: 'AUTO' }))?.requires_capability).toBeNull();
    expect(parseTarget(row({ requires_capability: true }))?.requires_capability).toBe(true);
  });

  it('reads the measured mode from whichever field the record carries it in, and never invents one', () => {
    expect(parseTarget(row({ requires_capability: 'auto' }))?.measured_mode).toBeNull();
    expect(parseTarget(row({ requires_capability: 'auto', measured_mode: 'BASE_ADVANCE' }))?.measured_mode).toBe('BASE_ADVANCE');
    expect(parseTarget(row({ requires_capability: 'auto', mode: 'ARM_ONLY' }))?.measured_mode).toBe('ARM_ONLY');
    // The runtime's own record carries the first execution's binding under `binding`, with its `mode`.
    expect(parseTarget(row({ requires_capability: 'auto', binding: { run_id: 'run-1', mode: 'BASE_ADVANCE' } }))?.measured_mode).toBe('BASE_ADVANCE');
    expect(parseTarget(row({ requires_capability: 'auto', binding: null }))?.measured_mode).toBeNull();
    expect(parseTarget(row({ requires_capability: 'auto', measured_mode: 7 }))?.measured_mode).toBeNull();
  });

  it('needs the capability only once it measured BASE_ADVANCE, and says so in the drawer', () => {
    const undecided = parseTarget(row({ requires_capability: 'auto' }));
    const far = parseTarget(row({ requires_capability: 'auto', binding: { mode: 'BASE_ADVANCE' } }));
    const near = parseTarget(row({ requires_capability: 'auto', binding: { mode: 'ARM_ONLY' } }));
    if (undecided === null || far === null || near === null) throw new Error('the rows did not parse');
    expect(targetRequiresCapability(undecided)).toBeNull();
    expect(targetRequiresCapability(far)).toBe(true);
    expect(targetRequiresCapability(near)).toBe(false);
    expect(targetCapabilityWords(undecided, 'mobile_approach')).toBe('whether it needs a capability is decided by measurement, and the robot has not measured it yet');
    expect(targetCapabilityWords(far, 'mobile_approach')).toBe('decided by measurement: it measured BASE_ADVANCE, so it needs the capability mobile_approach');
    expect(targetCapabilityWords(near, 'mobile_approach')).toBe('decided by measurement: it measured ARM_ONLY, so the arm alone places it and no capability is needed');
    // A mode word this console does not know is printed as the runtime wrote it.
    const other = parseTarget(row({ requires_capability: 'auto', measured_mode: 'SOME_NEWER_MODE' }));
    expect(targetCapabilityWords(other as NonNullable<typeof other>, null)).toBe('decided by measurement: it measured SOME_NEWER_MODE');
    // The declared values keep their words.
    expect(targetCapabilityWords(parseTarget(row({ requires_capability: true })) as NonNullable<typeof far>, 'mobile_approach')).toBe('requires the capability mobile_approach');
    expect(targetCapabilityWords(parseTarget(row({ requires_capability: false })) as NonNullable<typeof far>, null)).toBe('requires no capability');
    expect(targetCapabilityWords(parseTarget(row({})) as NonNullable<typeof far>, null)).toBe('whether it requires a capability is not recorded');
  });

  it('names the capability in the held sentence for an auto target that waits, because the wait itself says the need', () => {
    const target = parseTarget(row({ requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY', binding: { mode: 'BASE_ADVANCE' } }));
    const held = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', selected_target: 'cube', targets: [target as NonNullable<typeof target>] });
    const waiting = missionWaiting(held, 'mobile_approach');
    expect(waiting?.capability).toBe('mobile_approach');
    expect(waiting?.sentence).toContain('The block cannot be carried yet');
    expect(waiting?.sentence).toContain('(mobile_approach)');
    // And for one whose measured mode the record does not carry: the mission says it waits for the capability.
    const bare = parseTarget(row({ requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY' }));
    const heldBare = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', selected_target: 'cube', targets: [bare as NonNullable<typeof bare>] });
    expect(missionWaiting(heldBare, 'mobile_approach')?.capability).toBe('mobile_approach');
  });

  it('says what an arm-alone target waits for when its base fallback asked for the capability, never that it needs none', () => {
    const target = parseTarget(row({ requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY', binding: { mode: 'ARM_ONLY' } }));
    if (target === null) throw new Error('the row did not parse');
    const held = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', selected_target: target.target_id, targets: [target] });
    const words = targetCapabilityWords(target, 'mobile_approach', held);
    expect(words).toContain('the arm alone places it');
    expect(words).toContain('waits for the capability mobile_approach that its base fallback needs');
    expect(words).not.toContain('no capability is needed');
    // verify r6 policy MF2: the fallback is conditional on the way back bringing the loaded arm to the carry pose.
    expect(words).toContain('the base moves only if the loaded arm comes back to the carry pose');
    expect(words).toContain('otherwise the arm holds the cube where it stands for the operator');
    expect(words).not.toContain('should the loaded arm fall short');
    expect(missionWaiting(held, 'mobile_approach')?.capability).toBe('mobile_approach');
    // Held for an operator decision after a failed attempt, or another target selected: the measured sentence as before.
    for (const other of [mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision', selected_target: target.target_id, targets: [target] }),
      mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable', selected_target: 'another', targets: [target] })]) {
      expect(targetCapabilityWords(target, 'mobile_approach', other)).toBe('decided by measurement: it measured ARM_ONLY, so the arm alone places it and no capability is needed');
    }
  });
});

describe('the words for a state', () => {
  it('prints a token this console does not know as the runtime wrote it', () => {
    expect(targetStateWords('PLACED')).toBe('placed on the pad');
    expect(targetStateWords('SOME_NEWER_STATE')).toBe('SOME_NEWER_STATE');
    expect(missionStateWords('COMPLETE', null)).toBe('complete');
    expect(missionStateWords('SOME_NEWER_STATE', null)).toBe('SOME_NEWER_STATE');
  });

  it('says a hold by its reason, never a capability wait from the state alone', () => {
    // The runtime writes WAITING_FOR_CAPABILITY for a capability wait AND for the hold after a failed attempt; only the
    // reason tells them apart.
    const forCapability = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' });
    const forDecision = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision' });
    const unknown = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'some_newer_reason' });
    const unsaid = mission({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: null });

    expect(missionStateWords('WAITING_FOR_CAPABILITY', 'capability_unavailable')).toBe('held, waiting for a capability');
    expect(missionStateWords('WAITING_FOR_CAPABILITY', 'operator_decision')).toBe("held for the operator's decision after a failed attempt");
    expect(missionStateWords('WAITING_FOR_CAPABILITY', 'some_newer_reason')).toBe('held: the runtime gives the reason some_newer_reason');
    expect(missionStateWords('WAITING_FOR_CAPABILITY', null)).toBe('held: the runtime did not say why');

    expect(targetStateWords('WAITING_FOR_CAPABILITY', forCapability)).toBe('held, waiting for a capability');
    expect(targetStateWords('WAITING_FOR_CAPABILITY', forDecision)).toBe("held for the operator's decision after a failed attempt");
    expect(targetStateWords('WAITING_FOR_CAPABILITY', unknown)).toBe('held: the runtime gives the reason some_newer_reason');
    expect(targetStateWords('WAITING_FOR_CAPABILITY', unsaid)).toBe('held: the runtime did not say why');
    // With no mission record at all there is no reason to read, and no capability wait is claimed.
    expect(targetStateWords('WAITING_FOR_CAPABILITY')).toBe('held: the runtime did not say why');
  });
});

describe('the record is read, not guessed', () => {
  it('drops a target with no id: nothing could be attributed to it', () => {
    const parsed = parseMission({
      mission_id: 'm1',
      state: 'RUNNING',
      targets: [{ label: 'unlabelled', state: 'PENDING' }, { target_id: 'cube-a', label: 'Cube A', state: 'ACTIVE' }],
    });
    expect(parsed?.targets.map((target) => target.target_id)).toEqual(['cube-a']);
  });

  it('reads requires_capability as the BOOLEAN the runtime sends (finding F1)', () => {
    // Exactly the shape demo_runtime/mission_service.py writes: a bool, never a name.
    const parsed = parseMission({
      mission_id: 'm1',
      state: 'WAITING_FOR_CAPABILITY',
      data_mode: 'synthetic',
      performs_motion: false,
      execution_label: 'recording-rehearsal',
      targets: [
        { target_id: 'cube_near', label: 'the reachable cube', reachable: true, requires_capability: false, state: 'PLACED', runs: [] },
        { target_id: 'cube_far', label: 'the far cube', reachable: false, requires_capability: true, state: 'WAITING_FOR_CAPABILITY', runs: [] },
      ],
    });
    expect(parsed?.targets[0]?.requires_capability).toBe(false);
    // The far cube is the one target that needs a capability. It was parsed as null, and drawn as needing none.
    expect(parsed?.targets[1]?.requires_capability).toBe(true);
    expect(parsed?.performs_motion).toBe(false);
    expect(parsed?.execution_label).toBe('recording-rehearsal');
    // A string is not the runtime's shape and is not read as one.
    const odd = parseMission({ mission_id: 'm1', state: 'RUNNING', targets: [{ target_id: 'a', state: 'PENDING', requires_capability: 'x' }] });
    expect(odd?.targets[0]?.requires_capability).toBe(null);
  });

  it('reads an absent reachability as not determined rather than as a no', () => {
    const parsed = parseMission({ mission_id: 'm1', state: 'RUNNING', targets: [{ target_id: 'a', state: 'PENDING' }] });
    expect(parsed?.targets[0]?.reachable).toBe(null);
    expect(parsed?.targets[0]?.requires_capability).toBe(null);
    expect(parsed?.targets[0]?.runs).toEqual([]);
    // A target the runtime did not name is shown by its id, never by a label this console made up.
    expect(parsed?.targets[0]?.label).toBe('a');
  });

  it('is not a mission at all without an id and a state', () => {
    expect(parseMission({ state: 'RUNNING' })).toBe(null);
    expect(parseMission({ mission_id: 'm1' })).toBe(null);
    expect(parseMission(null)).toBe(null);
  });

  it('offers no candidate that has no key', () => {
    expect(parseCandidates({ ok: true, candidates: [{ payload_digest: 'sha256:aa' }, { key: 'k1', payload_digest: null }] })).toEqual([
      { key: 'k1', payload_digest: null, role: null, label: null, expect: null },
    ]);
  });

  it('keeps each target activations to that target', () => {
    const record = mission({
      activations: [
        {
          operation_uuid: 'op-1',
          target_id: 'cube-a',
          candidate_key: 'k1',
          payload_digest: null,
          activation_state: 'OBSERVED',
          outcome: null,
          transaction_phase: null,
          transaction_uuid: null,
          detail: null,
          t_wall_utc: null,
        },
        {
          operation_uuid: 'op-2',
          target_id: 'cube-b',
          candidate_key: 'k2',
          payload_digest: null,
          activation_state: 'SUBMITTED',
          outcome: null,
          transaction_phase: null,
          transaction_uuid: null,
          detail: null,
          t_wall_utc: null,
        },
      ],
    });
    expect(activationsForTarget(record, 'cube-b').map((item) => item.operation_uuid)).toEqual(['op-2']);
  });
});

describe('the hold after a failed attempt, as the runtime serves it', () => {
  const served = (nextActions: string[] | null) =>
    parseMission({
      mission_id: 'mission-0001', state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'operator_decision', selected_target: 'cube', targets: [],
      next_actions: nextActions ?? undefined,
      hold: nextActions === null ? null : {
        target_id: 'cube', attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'retryable', reason_code: 'grasp_missed', phase: 'GRASP_CLOSE',
        measurements: [], observation_ids: ['obs-1', 7], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: nextActions,
        budget_left: 1, t_wall_utc: '2026-09-27T00:00:00Z', retry_requested: false,
      },
    });

  it('reads the hold and its next actions as recorded, and nothing else', () => {
    const record = served(['stop']);
    expect(record?.hold?.next_actions).toEqual(['stop']);
    expect(record?.hold?.observation_ids).toEqual(['obs-1']);
    expect(record?.hold?.stop_state).toEqual({ holding: false, base_ahead: false, moved: true });
    expect(record?.next_actions).toEqual(['stop']);
    expect(served(null)?.hold).toBeNull();
    expect(served(null)?.next_actions).toBeNull();
  });

  it('offers a retry only when the hold lists one, and says Stop is the only offer otherwise', () => {
    expect(holdOffer(served(['retry', 'stop']))).toBe('retry');
    expect(holdOffer(served(['move_block_then_retry', 'stop']))).toBe('move_block_then_retry');
    expect(holdOffer(served(['stop']))).toBe('stop_only');
    expect(holdOffer(served(null))).toBe('not_served');
    expect(holdOffer(mission({ state: 'RUNNING' }))).toBeNull();
    const off = (record: MissionRecord | null) => missionStartOff({ mission: record, missionError: null, missionErrorDetail: null, runActive: false });
    expect(off(served(['stop']))).toContain('The runtime offers only Stop for this hold');
    expect(off(served(['stop']))).not.toContain('Try again');
    expect(off(served(['retry', 'stop']))).toContain('Try again parks the arm');
    expect(missionWaiting(served(['stop']))?.recovery).not.toContain('Try again');
    expect(missionWaiting(served(['retry', 'stop']))?.recovery).toContain('Try again');
  });

  it('turns off every command the hold does not list, in the hold\'s own words, and nothing once the hold is over', () => {
    // The runtime takes only Stop while a mission is carried, a held one included: a control is on beside a hold only
    // when the hold lists it, and the hold never lists Return arm, Scan scene or Observe.
    expect(holdBlock(served(['stop']), 'return_arm')).toBe("the runtime's hold offers stop, not Return arm");
    expect(holdBlock(served(['retry', 'stop']), 'return_arm')).toBe("the runtime's hold offers retry, stop, not Return arm");
    expect(holdBlock(served(['retry', 'stop']), 'scan_scene')).toContain('not Scan scene');
    expect(holdBlock(served(['retry', 'stop']), 'observe')).toContain('not Observe');
    expect(holdBlock(served(null), 'return_arm')).toContain('served no record of what its hold offers');
    expect(holdBlock(served(['return_arm', 'stop']), 'return_arm')).toBeNull();
    // Not held: nothing to say here (the runtime's list says the rest).
    expect(holdBlock(mission({ state: 'RUNNING' }), 'return_arm')).toBeNull();
    expect(holdBlock(null, 'return_arm')).toBeNull();
    // After a Stop the runtime serves STOPPED and keeps the waiting reason with no hold: the hold is over.
    const stopped = parseMission({ mission_id: 'mission-0001', state: 'STOPPED', waiting_reason: 'operator_decision', selected_target: null, targets: [], hold: null, next_actions: ['return_arm', 'start'] });
    expect(holdBlock(stopped, 'return_arm')).toBeNull();
    expect(holdOffer(stopped)).toBeNull();
  });
});

describe("Start, from the runtime's list of what the mission offers", () => {
  const off = (record: MissionRecord) => missionStartOff({ mission: record, missionError: null, missionErrorDetail: null, runActive: false });

  it('is on when the list offers start, whatever the mission ended as', () => {
    expect(off(mission({ state: 'COMPLETE', next_actions: ['start'] }))).toBeNull();
    expect(off(mission({ state: 'FAILED', next_actions: ['return_arm', 'start'] }))).toBeNull();
  });

  it("is off, in the list's words, when the list does not offer start, even beside a state that reads ended", () => {
    const refused = off(mission({ state: 'FAILED', next_actions: ['stop'] })) ?? '';
    expect(refused).toContain("runtime's mission offers stop, not Start");
    expect(off(mission({ state: 'RUNNING', next_actions: ['stop'] }))).not.toBeNull();
  });

  it('falls back to the mission state for a runtime that serves no list', () => {
    expect(off(mission({ state: 'COMPLETE', next_actions: null }))).toBeNull();
    expect(off(mission({ state: 'RUNNING', next_actions: null }))).not.toBeNull();
  });
});

