// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ONE MISTAKE A TWO-TARGET RAIL CAN MAKE.
//
// Two cubes are carried one after the other, through the same five steps, by the same controller emitting the same
// state names. This tab folds ONE of those runs at a time. So the five chips beside "Cube B" can be, without anything
// looking wrong on the screen, the five chips of Cube A's run: the same words, the same colours, the same verified
// ticks, against the wrong cube. Nothing downstream could catch it and nobody watching could tell.
//
// These tests pin the rule that prevents it: the rail answers for the mission's ACTIVE target, and it draws folded
// records only when the mission recorded that run against THAT target.

import { describe, expect, it } from 'vitest';
import { missionRail } from './steps5';
import type { MissionRecord } from '../model/missionRecord';
import type { PhaseInput } from '../model/phase';
import type { PredicateView } from '../model/types';

/** A fold that walked a run to a placed cube: the rail for it shows real verdicts. */
function placedFold(): PhaseInput {
  const predicates: PredicateView[] = [
    { predicate: 'object_secured', value: true, state: 'evaluated', seq: 10 },
    { predicate: 'placed', value: true, state: 'evaluated', seq: 20 },
  ] as unknown as PredicateView[];
  return {
    machine: { state: 'PLACED', since_seq: 20, history: [{ state: 'SEARCH', seq: 1 }, { state: 'PLACED', seq: 20 }] },
    predicates,
    action_history: [],
    active_run: null,
    run_started: { command_id: null, command_type: null, started_wall_utc: '2026-09-23T10:00:00.000Z' },
  } as unknown as PhaseInput;
}

function twoCubes(extra: Partial<MissionRecord> = {}): MissionRecord {
  return {
    mission_record_version: '1',
    mission_id: 'mission-0001',
    state: 'WAITING_FOR_CAPABILITY',
    data_mode: 'live',
    performs_motion: true,
    execution_label: 'physical',
    started_wall_utc: '2026-09-23T10:00:00.000Z',
    selected_target: 'cube-b',
    waiting_reason: 'capability_unavailable',
    detail: null,
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
    activations: [],
    ...extra,
  };
}

describe('the rail answers for the mission active target', () => {
  it('never draws one target folded records under another target name', () => {
    // The fold is Cube A's finished run. The mission has moved on to Cube B and is waiting.
    const rail = missionRail(twoCubes(), placedFold(), 'run-a1');
    expect(rail.target?.target_id).toBe('cube-b');
    expect(rail.scoped).toBe(false);
    // Not one verified step. Cube B has been nowhere.
    expect(rail.rows.map((row) => row.status)).toEqual(['not_reached', 'not_reached', 'not_reached', 'not_reached', 'not_reached']);
    expect(rail.rows.every((row) => row.words === 'Not yet')).toBe(true);
  });

  it('draws the folded records when the mission recorded that run against this target', () => {
    const onCubeA = twoCubes({ state: 'RUNNING', selected_target: 'cube-a' });
    const rail = missionRail(onCubeA, placedFold(), 'run-a1');
    expect(rail.target?.target_id).toBe('cube-a');
    expect(rail.scoped).toBe(true);
    expect(rail.rows.find((row) => row.step === 'place')?.status).toBe('verified');
    expect(rail.basis).toContain('run-a1');
  });

  it('says "not seen from here" for a target that has run and whose run this tab is not folding', () => {
    // Cube B has a run of its own; this tab is folding Cube A's. "Not yet" would be a claim about the robot made out
    // of this tab's own ignorance, which is the distinction the phase table already draws.
    const later = twoCubes({
      selected_target: 'cube-b',
      targets: [
        { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: ['run-a1'] },
        { target_id: 'cube-b', label: 'Cube B', reachable: true, requires_capability: null, state: 'ACTIVE', runs: ['run-b1'] },
      ],
    });
    const rail = missionRail(later, placedFold(), 'run-a1');
    expect(rail.target?.target_id).toBe('cube-b');
    expect(rail.scoped).toBe(false);
    expect(rail.rows.every((row) => row.words === 'Not seen from here')).toBe(true);
    expect(rail.basis).toContain('another target');
  });

  it('keeps its five steps and their labels whichever target it is about', () => {
    const scoped = missionRail(twoCubes({ state: 'RUNNING', selected_target: 'cube-a' }), placedFold(), 'run-a1');
    const unscoped = missionRail(twoCubes(), placedFold(), 'run-a1');
    const labels = ['Find', 'Pick', 'Carry', 'Place', 'Return'];
    expect(scoped.rows.map((row) => row.label)).toEqual(labels);
    expect(unscoped.rows.map((row) => row.label)).toEqual(labels);
    expect(unscoped.rows.map((row) => row.step)).toEqual(['find', 'pick', 'carry', 'place', 'return']);
  });

  it('is the plain live fold, unchanged, for a runtime that carries no mission', () => {
    const rail = missionRail(null, placedFold(), 'run-a1');
    expect(rail.target).toBe(null);
    expect(rail.scoped).toBe(true);
    expect(rail.rows.find((row) => row.step === 'place')?.status).toBe('verified');
  });

  it('answers for the run own target once the mission names no active one', () => {
    const done = twoCubes({
      state: 'COMPLETE',
      selected_target: null,
      targets: [
        { target_id: 'cube-a', label: 'Cube A', reachable: true, requires_capability: null, state: 'PLACED', runs: ['run-a1'] },
        { target_id: 'cube-b', label: 'Cube B', reachable: true, requires_capability: null, state: 'PLACED', runs: ['run-b1'] },
      ],
    });
    const rail = missionRail(done, placedFold(), 'run-b1');
    expect(rail.target?.target_id).toBe('cube-b');
    expect(rail.scoped).toBe(true);
  });
});
