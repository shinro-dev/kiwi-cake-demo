// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AT A MISSION'S NEXT RUN THE SCENE SAYS WHAT THAT RUN'S RECORD SAYS ABOUT THE BLOCK (redesign r4 verify-r3 must_fix 2).
//
// Refix round 3 kept the block the earlier run saw on the map at the next run, as that run's, and said "Run <new> has not
// reported what it sees yet" while the new run had no sighting of its own. It never asked whether the new run had
// answered at all or whether it had ended: after the new run's cube layers answered not_detected or error, and after its
// run.finished, the Scene still said the run "has not reported what it sees yet". Folded here over a SYNTHETIC mission of
// two runs in the runtime's record shape (`model/syntheticMissionRecords.ts`; round 7 item 5 replaced a fixture of raw
// runtime records with it, same shape, every id, time and value invented), as they are and with the edits each case
// names: the controller emits not_detected on the cube-in-pad-frame layer when the pad frame is valid and the cube cannot
// be placed, and error when the pad frame is not valid (`bin/laptop/manipulation/run_single_cube_cycle.py`,
// `_perception_layers`).

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ContractEvent } from '../contracts/envelope';
import { emptySceneMap, foldSceneMap } from '../model/sceneMap';
import type { NewestLook, SceneBlock } from '../model/sceneMap';
import { sceneRecordsLine } from '../model/sceneRecords';
import {
  SYNTHETIC_MISSION_RUN_1 as RUN_1_RECORDS,
  SYNTHETIC_MISSION_RUN_2_FINISHED as RUN_2_FINISHED,
  SYNTHETIC_MISSION_RUN_2_TO_FIRST_SIGHTING as RUN_2_TO_FIRST_SIGHTING,
  SYNTHETIC_RUN_1,
  SYNTHETIC_RUN_2,
} from '../model/syntheticMissionRecords';
import { newestRunWaitingWords, SceneMap } from './SceneMap';
import { element, textOf } from './demoScenarioPlay';

const RUN_1 = SYNTHETIC_RUN_1;
const RUN_2 = SYNTHETIC_RUN_2;
const DAY = '2026-01-15';
const CUBE_LAYERS = ['cube-recognizer', 'cube-in-pad-frame'];

/** Run 2's records up to `seq`, as recorded. */
function upTo(seq: number): ContractEvent[] {
  return RUN_2_TO_FIRST_SIGHTING.filter((event) => event.seq <= seq);
}

/**
 * Run 2's records to its first sighting with its first cube-layer answers set to `recognizer` and `inPadFrame` (the judge's
 * DERIVED edit): the result, no confidence, and every measurement read as not measured.
 */
function edited(recognizer: string, inPadFrame: string): ContractEvent[] {
  return RUN_2_TO_FIRST_SIGHTING.map((event) => {
    if (event.kind !== 'perception.result' || !CUBE_LAYERS.includes(event.payload.detector)) return event;
    const measurements = event.payload.measurements.map((item) => ({ ...item, value: typeof item.value === 'boolean' ? false : null }));
    const result = event.payload.detector === 'cube-recognizer' ? recognizer : inPadFrame;
    return { ...event, payload: { ...event.payload, result, confidence: null, measurements } } as ContractEvent;
  });
}

/** The Scene over run 1 whole and then `run2`, drawn as the demo screen draws it: its text and its waiting line. */
function scene(run2: readonly ContractEvent[]): { text: string; waiting: string | null; blocks: { run: string | null; earlier: boolean }[] } {
  const runs = [RUN_1_RECORDS, run2].map((events) => ({
    run_id: events[0]?.run_id ?? null,
    command_type: 'start_cycle',
    started_wall_utc: events.find((event) => event.kind === 'run.started')?.t_wall_utc ?? null,
    events,
  }));
  const map = foldSceneMap(runs.flatMap((run) => run.events));
  const markup = renderToStaticMarkup(createElement(SceneMap, { map, slot: null, title: 'Scene', drawnFrom: sceneRecordsLine(runs as never, DAY), day: DAY }));
  const waiting = element(markup, 'data-scene-waiting=""');
  return { text: textOf(markup), waiting: waiting === null ? null : textOf(waiting), blocks: map.blocks.map((block) => ({ run: block.run_id, earlier: block.earlier })) };
}

const NOT_YET = 'has not reported what it sees yet';

describe('the Scene over a synthetic mission of two runs in the runtime\'s record shape', () => {
  it('the records have the shape the cases below fold: 31, 22 and 1 records, run 2\'s two cube-layer answers last', () => {
    expect(RUN_1_RECORDS.map((event) => event.seq)).toEqual(Array.from({ length: 31 }, (_, index) => 101 + index));
    expect(RUN_2_TO_FIRST_SIGHTING.map((event) => event.seq)).toEqual(Array.from({ length: 22 }, (_, index) => 132 + index));
    expect(RUN_2_FINISHED).toMatchObject({ seq: 414, kind: 'run.finished', run_id: RUN_2 });
    const answers = RUN_2_TO_FIRST_SIGHTING.filter((event) => event.kind === 'perception.result' && CUBE_LAYERS.includes(event.payload.detector));
    expect(answers.map((event) => event.seq)).toEqual([150, 153]);
  });

  it('CONTROL, run 2 to seq 148 (no cube-layer answer yet): run 1\'s block, with the waiting line', () => {
    const drawn = scene(upTo(148));
    expect(drawn.blocks).toEqual([{ run: RUN_1, earlier: true }]);
    expect(drawn.waiting).toBe(`Run ${RUN_2} has not reported what it sees yet; the block drawn is what run ${RUN_1} saw.`);
  });

  it('CONTROL, run 2 to seq 153 (its detection): run 2\'s own block, and no waiting line', () => {
    const drawn = scene(upTo(153));
    expect(drawn.blocks).toEqual([{ run: RUN_2, earlier: false }]);
    expect(drawn.waiting).toBeNull();
    expect(drawn.text).not.toContain(NOT_YET);
  });

  it('DERIVED, run 2\'s cube layers answered not_detected: says run 2 looked and did not see a block, never that it has not reported', () => {
    const drawn = scene(edited('not_detected', 'not_detected'));
    expect(drawn.blocks).toEqual([{ run: RUN_1, earlier: true }]);
    expect(drawn.text).not.toContain(NOT_YET);
    expect(drawn.waiting).toBe(
      `Run ${RUN_2} looked at 10:06:40Z and did not see a block (cube-recognizer: not_detected, cube-in-pad-frame: not_detected); the block drawn is what run ${RUN_1} saw.`,
    );
  });

  it('DERIVED, the same and then run 2\'s own run.finished: says the run has ended, and never "yet"', () => {
    const drawn = scene([...edited('not_detected', 'not_detected'), RUN_2_FINISHED]);
    expect(drawn.blocks).toEqual([{ run: RUN_1, earlier: true }]);
    expect(drawn.text).not.toContain(NOT_YET);
    expect(drawn.waiting).not.toMatch(/\byet\b/);
    expect(drawn.waiting).toBe(
      `Run ${RUN_2} looked at 10:06:40Z and did not see a block (cube-recognizer: not_detected, cube-in-pad-frame: not_detected), and the run has ended; the block drawn is what run ${RUN_1} saw.`,
    );
  });

  it('DERIVED, recognizer not_detected and cube-in-pad-frame error (no valid pad frame): says what both layers answered', () => {
    const drawn = scene(edited('not_detected', 'error'));
    expect(drawn.text).not.toContain(NOT_YET);
    expect(drawn.waiting).toBe(
      `Run ${RUN_2} looked at 10:06:40Z and did not see a block (cube-recognizer: not_detected, cube-in-pad-frame: error); the block drawn is what run ${RUN_1} saw.`,
    );
  });

  it('DERIVED, recognizer detected and cube-in-pad-frame error: says run 2 could not locate the block in the pad frame', () => {
    const drawn = scene(edited('detected', 'error'));
    expect(drawn.blocks).toEqual([{ run: RUN_1, earlier: true }]);
    expect(drawn.text).not.toContain(NOT_YET);
    expect(drawn.waiting).toBe(
      `Run ${RUN_2} could not locate a block in the pad frame at 10:06:40Z (cube-recognizer: detected, cube-in-pad-frame: error); the block drawn is what run ${RUN_1} saw.`,
    );
  });

  it('DERIVED, run 2 to seq 142 (no perception) and then its run.finished: says it ended without reporting a sighting', () => {
    const drawn = scene([...upTo(142), RUN_2_FINISHED]);
    expect(drawn.blocks).toEqual([{ run: RUN_1, earlier: true }]);
    expect(drawn.text).not.toContain(NOT_YET);
    expect(drawn.waiting).toBe(`Run ${RUN_2} ended without reporting a sighting; the block drawn is what run ${RUN_1} saw.`);
  });

  it('run 2 to seq 152 (its recognizer detected, its pad-frame layer not yet): says it saw a block and has not said where', () => {
    const drawn = scene(upTo(152));
    expect(drawn.blocks).toEqual([{ run: RUN_1, earlier: true }]);
    expect(drawn.text).not.toContain(NOT_YET);
    expect(drawn.waiting).toBe(`Run ${RUN_2} saw a block at 10:06:40Z and has not reported where it is yet (cube-recognizer: detected); the block drawn is what run ${RUN_1} saw.`);
  });
});

describe('the line beside an earlier run\'s block, for every kind of answer the newest run can give', () => {
  const kept: SceneBlock = {
    point_mm: [-8.3, -178.9], class: 'BASE_ADVANCE', sentence: '', source: 'search', observation_id: null, views_seen: 1, spread_mm: null,
    inferred: false, selected: false, details: [], run_id: 'run-1', seen_wall_utc: '2026-09-28T05:00:01.000Z', earlier: true,
  };
  const words = (look: NewestLook): string | null => newestRunWaitingWords({ ...emptySceneMap(), blocks: [kept], newest_run: 'run-2', newest_look: look }, '2026-09-28');
  const answer = (detector: string, result: string) => ({ detector, result, observation_id: 'obs-2', t_wall_utc: '2026-09-28T05:00:07.500Z' });
  const DRAWN = 'the block drawn is what run run-1 saw.';

  it('says a scan that reported views and no block so, and that it has ended once it has', () => {
    expect(words({ frame: [], views: 3, ended: false })).toBe(`Run run-2 has reported 3 scan views and no block yet; ${DRAWN}`);
    expect(words({ frame: [], views: 1, ended: true })).toBe(`Run run-2 reported 1 scan view and no block, and the run has ended; ${DRAWN}`);
  });

  it('says a block located outside a SEARCH or a Look is one this map does not draw', () => {
    expect(words({ frame: [answer('cube-recognizer', 'detected'), answer('cube-in-pad-frame', 'detected')], views: 0, ended: false })).toBe(
      `Run run-2 located a block in the pad frame at 05:00:07Z outside a SEARCH or a Look, which this map does not draw (cube-recognizer: detected, cube-in-pad-frame: detected); ${DRAWN}`,
    );
  });

  it('gives any other recognizer answer as the record gives it, and never "yet" once the run has ended', () => {
    expect(words({ frame: [answer('cube-recognizer', 'ambiguous')], views: 0, ended: false })).toBe(`Run run-2 looked at 05:00:07Z and reported no block (cube-recognizer: ambiguous); ${DRAWN}`);
    expect(words({ frame: [answer('cube-recognizer', 'detected')], views: 0, ended: true })).toBe(
      `Run run-2 saw a block at 05:00:07Z and did not report where it is (cube-recognizer: detected), and the run has ended; ${DRAWN}`,
    );
    expect(words({ frame: [], views: 0, ended: false })).toBe(`Run run-2 has not reported what it sees yet; ${DRAWN}`);
    expect(words({ frame: [], views: 0, ended: true })).toBe(`Run run-2 ended without reporting a sighting; ${DRAWN}`);
  });
});
