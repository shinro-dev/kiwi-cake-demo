// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SCENARIO CATALOGUE OF THE SIMULATION (docs/autonomous-demo/overnight-plan-2026-09-26.md, track U2). One scenario
// per failure mode of the live session of 2026-09-26 and per success, and one per new behaviour, each chosen by the
// address (`?source=simulation&scenario=<name>`), each playing nothing until the operator presses what starts it, and
// each carrying the controller's state names, field shapes and the day's numbers. These pin, per scenario, the
// transitions, the reason code, the values and the mission states, with numbers written here and not read back out of
// the catalogue, so a scenario that drifted from the day it reproduces fails here.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import controllerSource from '../../../../bin/laptop/manipulation/run_single_cube_cycle.py?raw';
import type { Command } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import { scenarioFrom } from '../panels/sourceChoice';
import { SyntheticSource } from './synthetic';
import {
  HOLD_FOR_OPERATOR_STATE,
  RETRYABLE_CLASS,
  OPERATOR_DECISION,
  SCENARIO_CATALOGUE,
  SCENARIO_NAMES,
  buildParkRun,
  scenarioSpec,
  scenarioUrl,
  syntheticRobotBody,
} from './syntheticScenarios';
import type { ScenarioAttemptRecord, ScenarioName } from './syntheticScenarios';
import type { PreviewSample, TelemetryMessage } from './types';

/* ---- the harness ------------------------------------------------------------------------------------------------ */

interface Drive {
  source: SyntheticSource;
  events: ContractEvent[];
  missionStates: string[];
  previews: PreviewSample[];
  off: () => void;
}

function drive(name: ScenarioName): Drive {
  const source = new SyntheticSource({ scenario: name });
  const events: ContractEvent[] = [];
  const previews: PreviewSample[] = [];
  const offEvents = source.subscribeEvents(0, (message) => {
    if ('event' in message) events.push(message.event);
  });
  const offTelemetry = source.subscribeTelemetry((message: TelemetryMessage) => {
    if ('preview' in message) previews.push(message.preview);
  });
  return {
    source,
    events,
    missionStates: [],
    previews,
    off: () => {
      offEvents();
      offTelemetry();
    },
  };
}

/** Advance the fake clock until `count` run.finished records arrived, noting every mission state on the way. */
async function play(run: Drive, count: number): Promise<void> {
  for (let slice = 0; slice < 1200; slice += 1) {
    const mission = await run.source.mission();
    if (mission.ok) {
      const state = mission.mission.state;
      if (run.missionStates[run.missionStates.length - 1] !== state) run.missionStates.push(state);
    }
    if (finished(run.events).length >= count) return;
    await vi.advanceTimersByTimeAsync(250);
  }
  throw new Error(`the scenario did not reach ${String(count)} run.finished record(s)`);
}

async function noteMission(run: Drive): Promise<void> {
  const mission = await run.source.mission();
  if (mission.ok && run.missionStates[run.missionStates.length - 1] !== mission.mission.state) run.missionStates.push(mission.mission.state);
}

function transitions(events: ContractEvent[]): string[] {
  return events.filter((event) => event.kind === 'state.transition').map((event) => (event.payload as { to_state: string }).to_state);
}

function finished(events: ContractEvent[]): Array<{ outcome: string; exit_status?: number; reason_code?: string | null; phase?: string | null; automatic_retries: number; failure?: { detail: string; measurements: Array<{ name: string; value: unknown }>; observation_ids: string[] } | null }> {
  return events.filter((event) => event.kind === 'run.finished').map((event) => event.payload as never);
}

/** The fields of every `[STATE] <name> {...}` line, in order. */
function stateLines(events: ContractEvent[], name: string): Array<Record<string, unknown>> {
  const prefix = `[STATE] ${name} `;
  return events
    .filter((event) => event.kind === 'diagnostic.text')
    .map((event) => (event.payload as { text: string }).text)
    .filter((text) => text.startsWith(prefix))
    .map((text) => JSON.parse(text.slice(prefix.length)) as Record<string, unknown>);
}

function perceptions(events: ContractEvent[], detector: string): Array<{ result: string; observation_ids: string[]; measurements: Array<{ name: string; value: unknown; unit: string | null }> }> {
  return events.filter((event) => event.kind === 'perception.result' && (event.payload as { detector: string }).detector === detector).map((event) => event.payload as never);
}

function measurement(list: Array<{ name: string; value: unknown }> | undefined, name: string): unknown {
  return list?.find((item) => item.name === name)?.value;
}

function predicates(events: ContractEvent[], name: string): Array<{ value: boolean | null; measured: number | null; threshold: number | null }> {
  return events.filter((event) => event.kind === 'predicate.evaluated' && (event.payload as { predicate: string }).predicate === name).map((event) => event.payload as never);
}

function command(type: string): Command {
  return { contract_version: '1', command_id: `test-${type}`, type, args: {}, client: { session_id: 'test', contract_version: '1' } } as unknown as Command;
}

const PLAN_NAMES = [
  'near-block-grasp-miss',
  'far-block-success',
  'far-left-yawed-success',
  'not-placeable-side',
  'not-placeable-budget',
  'align-no-proposal',
  'align-clipped-top',
  'observe-landmarks-only',
  'preview-stall',
  'preview-not-configured',
  'abort-swap-repeated',
  'bring-up-not-confirmed',
  'host-not-supervised',
  'scene-scan',
  'hold-then-retry-placed',
  'hold-live-default',
  'tampered-refused-before-transaction',
  'undo-to-inert',
  'swap-then-stopped-holding',
];

/**
 * WHAT THE CONTROLLER SENDS, as the four recorded events files of 2026-09-26 carry it (m8c5f9e9, mb3c1048, m2476137 and
 * the Observe 0i5xpdmx): the (name, unit) list of each perception layer by its result, and the field names of each
 * [STATE] line. Written here from those records, not read back out of the scenarios.
 */
const CUBE_BOX: Array<[string, string | null]> = [
  ['bbox_x0', 'px'],
  ['bbox_y0', 'px'],
  ['bbox_x1', 'px'],
  ['bbox_y1', 'px'],
  ['contact_x', 'px'],
  ['contact_y', 'px'],
  ['contact_available', null],
];
const CLIPPED: Array<[string, string | null]> = [
  ['clipped_any_border', null],
  ['clipped_bottom', null],
  ['clipped_left', null],
  ['clipped_occluded_hint', null],
  ['clipped_right', null],
  ['clipped_top', null],
];
const OUTLINE: Array<[string, string | null]> = [
  ['boundary_rms', 'px'],
  ['visible_fraction', null],
  ['view_elevation', 'deg'],
];
const LANDMARKS: Array<[string, string | null]> = [
  ['template_available', null],
  ['pad_identity', null],
  ['pad_frame_valid', null],
  ['landmark_inliers', null],
  ['reprojection_rms', 'px'],
];
const CUBE_IN_PAD: Array<[string, string | null]> = [
  ['cube_center_pad_x', 'm'],
  ['cube_center_pad_y', 'm'],
  ['inside_pad_outline', null],
];
const RECORDED_LAYERS: Record<string, Record<string, Array<[string, string | null]>>> = {
  'cube-recognizer': { detected: [...CUBE_BOX, ...CLIPPED], not_detected: CUBE_BOX },
  'pad-outline-fit': { detected: OUTLINE, not_detected: OUTLINE },
  'pad-print-landmarks': { detected: LANDMARKS, not_detected: LANDMARKS, error: [['template_available', null]] },
  'cube-in-pad-frame': { detected: CUBE_IN_PAD, not_detected: CUBE_IN_PAD, error: CUBE_IN_PAD },
};
/** The scene results of track S (plan section 2.4), which no recorded run carries yet. */
const SCENE_DETECTORS = ['scene-view', 'scene-block', 'scene-pad'];

const RECORDED_STATE_FIELDS: Record<string, string[]> = {
  SEARCH: ['pose'],
  // policy_search._search_from_scene: the low-pose frame decides, or the scene's own views are taken.
  SEARCH_SCENE: ['centre_class', 'centre_listing', 'early_exit'],
  // The pad policy of 2026-09-29: candidates (blocks off the pad) and on_pad_ignored (blocks on it) replace occupied_pad_m.
  TARGET_SELECTED: ['bound_pad_m', 'candidates', 'cubes_listed', 'distance_to_binding_mm', 'listed_pass', 'mode', 'on_pad_ignored', 'seen_pad_m', 'target_id', 'targets_bound'],
  PREGRASP: ['coarse', 'cube_inside_pad_mm'],
  ALIGN: ['aligned', 'attempt', 'lateral_mm', 'radial_mm', 'tool'],
  APPROACH: ['radial_mm'],
  STRADDLING: ['tool'],
  GRASP_CLOSE: ['attempt', 'closed_on_something', 'gripper', 'tool'],
  GRASP_RETRY_ADVANCE: ['to_forward_mm'],
  OBJECT_SECURED: ['gripper', 'retention_score', 'threshold', 'value'],
  PLACEMENT_PLAN: ['anchor_margin_mm', 'anchor_pad_m', 'base_advance_mm', 'bound_mode', 'fk_travel_mm', 'mode', 'reach_direction_pad', 'required_centre_margin_mm'],
  CARRY: ['mode', 'tool'],
  HOLD_OBSERVE: ['anchor_margin_mm', 'anchor_pad_m', 'pad_frame_valid'],
  BASE_ADVANCE: ['assumed_rate_mm_s', 'axis_pad', 'duration_s', 'needed_mm', 'pulse'],
  BASE_MEASURED: ['advance_mm', 'distortion_mm', 'duration_s', 'lateral_mm', 'rate_mm_s', 'vx_mps'],
  RELEASE_ADMISSION: ['admitted', 'anchor_pad_m', 'base_advance_measured_mm', 'fk_travel_from_grasp_mm', 'max_release_height_mm', 'release_height_mm', 'required_mm', 'tool', 'worst_case_centre_margin_mm'],
  RELEASED: ['fk_travel_from_grasp_mm', 'gripper', 'tool'],
  // The release agreement (the pad policy of 2026-09-29): on_pad_cubes, release_distance_mm and release_tolerance_mm replace
  // unclaimed_placed_cubes and earlier_targets_undisturbed (run_single_cube_cycle.py, _mission_placed_fields).
  PLACED: ['attributed', 'attribution_reason', 'centre_to_edge_mm', 'clearance_target_met', 'clearance_target_mm', 'cube_pad_m', 'footprint_margin_any_yaw_mm', 'on_pad_cubes', 'predicted_worst_case_centre_margin_mm', 'release_distance_mm', 'release_tolerance_mm', 'target_id', 'value'],
  BASE_RETURN: ['duration_s', 'pulse', 'remaining_mm'],
  BASE_RETURNED: ['remaining_along_axis_mm', 'sideways_mm', 'tolerance_mm', 'value'],
};

describe("the mission [STATE] fields are the controller's own (the pad policy of 2026-09-29)", () => {
  it('TARGET_SELECTED names what _mission_search writes', () => {
    const from = controllerSource.indexOf('self.state("TARGET_SELECTED"');
    const call = controllerSource.slice(from, controllerSource.indexOf('evidence=record)', from));
    const keys = [...call.matchAll(/\b([a-z_]+)=/g)].map((match) => match[1]).filter((key): key is string => key !== undefined);
    expect([...new Set(keys)].sort()).toEqual(RECORDED_STATE_FIELDS['TARGET_SELECTED']);
  });

  it('PLACED carries what _mission_placed_fields returns, beside the placement fields', () => {
    const from = controllerSource.indexOf('    def _mission_placed_fields(');
    const body = controllerSource.slice(from, controllerSource.indexOf('    def _mission_predicates(', from));
    const keys = [...body.matchAll(/"([a-z_]+)": /g)].map((match) => match[1]).filter((key): key is string => key !== undefined);
    expect(keys.sort()).toEqual(['attributed', 'attribution_reason', 'on_pad_cubes', 'release_distance_mm', 'release_tolerance_mm', 'target_id']);
    for (const key of keys) expect(RECORDED_STATE_FIELDS['PLACED'], key).toContain(key);
  });
});

/** Every event of every run a plan scenario can play: its flow, its Look, its scan and the park before a retry. */
function everyPlannedRun(name: string): ContractEvent[] {
  const spec = scenarioSpec(name);
  if (spec === null) throw new Error(`no scenario ${name}`);
  // The park before a retry is the same run for every scenario that holds.
  const factories = [...spec.flow.map((step) => step.build), ...(spec.look === undefined ? [] : [spec.look]), ...(spec.scan === undefined ? [] : [spec.scan]), buildParkRun];
  return factories.flatMap((build, index) => build(index + 1, 1000 * (index + 1), 0).steps.map((step) => step.event));
}

/**
 * THE HOLD AFTER A LOST BLOCK, AS THE HEAD'S RUNTIME HOLDS IT (mission_coordinator at 05fcea0, which can park): retryable,
 * nothing held, the base home, one retry offered. The retry parks the arm and the fresh attempt plays the same loss (a
 * rehearsal has no second record), so the second hold offers only Stop with the budget spent.
 */
async function expectTheHeadsHold(run: Drive): Promise<void> {
  await noteMission(run);
  expect(run.missionStates).toEqual(['RUNNING', 'WAITING_FOR_CAPABILITY']);
  const held = await run.source.mission();
  if (!held.ok) throw new Error('no mission');
  expect(held.mission.waiting_reason).toBe(OPERATOR_DECISION);
  expect(held.mission.hold).toMatchObject({ attempt: 1, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN', budget_left: 1, park_run_id: null });
  expect(held.mission.hold?.stop_state).toEqual({ holding: false, base_ahead: false, moved: true });
  expect(held.mission.hold?.next_actions).toEqual(['retry', 'stop']);
  expect((await run.source.missionRetry()).kind).toBe('started');
  await play(run, 3);
  expect(finished(run.events).map((item) => item.outcome)).toEqual(['failed', 'unknown', 'failed']);
  await noteMission(run);
  expect(run.missionStates).toEqual(['RUNNING', 'WAITING_FOR_CAPABILITY', 'RUNNING', 'WAITING_FOR_CAPABILITY']);
  const spent = await run.source.mission();
  if (!spent.ok) throw new Error('no mission');
  expect(spent.mission.hold).toMatchObject({ attempt: 2, budget_left: 0, next_actions: ['stop'], park_run_id: spent.mission.targets[0]?.runs[1] });
  expect(await run.source.missionRetry()).toMatchObject({ kind: 'refused', reason_code: 'mission_retry_not_offered' });
}

/* ---- the catalogue ---------------------------------------------------------------------------------------------- */

describe('the scenario catalogue', () => {
  it('carries every scenario the plan names, and the one-block rehearsal before them', () => {
    expect([...SCENARIO_NAMES]).toEqual(['one-block', ...PLAN_NAMES]);
    expect(SCENARIO_CATALOGUE.map((spec) => spec.name)).toEqual([...SCENARIO_NAMES]);
  });

  it('selects each one by the address, and only with the simulation named', () => {
    for (const name of SCENARIO_NAMES) {
      expect(scenarioUrl(name)).toBe(`?source=simulation&scenario=${name}`);
      expect(scenarioFrom(scenarioUrl(name))).toBe(name);
      expect(scenarioFrom(`?scenario=${name}`)).toBeNull();
    }
    expect(scenarioFrom('?source=simulation&scenario=no-such-scenario')).toBeNull();
    expect(scenarioSpec('no-such-scenario')).toBeNull();
  });

  it('says for every scenario what it reproduces, what the screen shows and what the operator presses first', () => {
    for (const spec of SCENARIO_CATALOGUE) {
      expect(spec.title.length, spec.name).toBeGreaterThan(0);
      expect(spec.reproduces.length, spec.name).toBeGreaterThan(0);
      expect(spec.shows.length, spec.name).toBeGreaterThan(0);
      expect(['start', 'look', 'check', 'scan'], spec.name).toContain(spec.trigger);
    }
  });

  it('names the run or the behaviour behind each scenario of today', () => {
    expect(scenarioSpec('near-block-grasp-miss')?.reproduces).toContain('m8c5f9e9');
    expect(scenarioSpec('far-block-success')?.reproduces).toContain('mb3c1048');
    expect(scenarioSpec('far-left-yawed-success')?.reproduces).toContain('m2476137');
    expect(scenarioSpec('observe-landmarks-only')?.reproduces).toContain('0i5xpdmx');
    expect(scenarioSpec('align-no-proposal')?.reproduces).toContain('m76fe86c');
    expect(scenarioSpec('align-clipped-top')?.reproduces).toContain('m66738ff');
  });

  it("keeps the recognizer's words apart from the console's inference in the two ALIGN losses", () => {
    for (const name of ['align-no-proposal', 'align-clipped-top'] as const) {
      expect(scenarioSpec(name)?.shows, name).toContain("the recognizer's own reason: no detector proposal: nothing to verify");
    }
    const top = scenarioSpec('align-clipped-top')?.shows ?? '';
    expect(top).toContain("the console's inference");
    expect(top).not.toContain('rejected');
    expect(scenarioSpec('align-no-proposal')?.shows).toContain('one pan trim');
  });

  it('states the day as it was measured: the ALIGN radial, not the coarse estimate, and no gate on the sideways return', () => {
    const near = scenarioSpec('near-block-grasp-miss')?.shows ?? '';
    expect(near).toContain('about 18 mm inside the proven radius');
    expect(near).toContain('the coarse estimate said 44.5');
    expect(near).toContain('refused at SEARCH before any motion');
    const farLeft = scenarioSpec('far-left-yawed-success')?.shows ?? '';
    expect(farLeft).toContain('9.1 mm sideways, which no gate checks');
    expect(farLeft).not.toContain('tolerance');
  });

  describe("speaks the controller's own perception layers and [STATE] fields in every plan scenario", () => {
    for (const name of PLAN_NAMES) {
      it(name, () => {
        const events = everyPlannedRun(name);
        for (const event of events) {
          if (event.kind === 'perception.result') {
            const payload = event.payload as { detector: string; result: string; measurements: Array<{ name: string; unit: string | null }> };
            if (SCENE_DETECTORS.includes(payload.detector)) continue;
            const layer = RECORDED_LAYERS[payload.detector];
            expect(layer, `${name}: detector ${payload.detector}`).toBeDefined();
            expect(layer?.[payload.result], `${name}: ${payload.detector} ${payload.result}`).toBeDefined();
            expect(payload.measurements.map((item) => [item.name, item.unit]), `${name}: ${payload.detector} ${payload.result}`).toEqual(layer?.[payload.result]);
          }
          if (event.kind === 'diagnostic.text' && (event.payload as { text: string }).text.startsWith('[STATE] ')) {
            const text = (event.payload as { text: string }).text.slice('[STATE] '.length);
            const state = text.slice(0, text.indexOf(' '));
            const fields = RECORDED_STATE_FIELDS[state];
            if (fields === undefined) continue;
            expect(Object.keys(JSON.parse(text.slice(state.length + 1)) as object).sort(), `${name}: [STATE] ${state}`).toEqual(fields);
          }
        }
      });
    }
  });

  it("states every run's words as the head's run.config does, and sweeps only under search_sweep", () => {
    const STAGES = ['search', 'align', 'grasp', 'placement', 'return'];
    for (const name of SCENARIO_NAMES) {
      const events = everyPlannedRun(name);
      const runs = new Map<string, ContractEvent[]>();
      for (const event of events) runs.set(String(event.run_id), [...(runs.get(String(event.run_id)) ?? []), event]);
      for (const [runId, own] of runs) {
        const config = own.find((event) => event.kind === 'run.config');
        const summary = (config?.payload as { summary?: Array<{ name: string; value: unknown }> } | undefined)?.summary ?? [];
        const selection = (stage: string): unknown => summary.find((item) => item.name === `policy.${stage}.selection`)?.value;
        // run_single_cube_cycle.stage_policy_summary: one selection per stage, 'proven' for a stage run with none of its words.
        for (const stage of STAGES) expect(typeof selection(stage), `${name} ${runId} ${stage}`).toBe('string');
        expect(summary.some((item) => item.name === 'placement_envelope'), `${name} ${runId}`).toBe(false);
        // policy_search: the SEARCH SWEEP runs only under the word search_sweep (486d8a6).
        if (transitions(own).includes('SEARCH_SWEEP')) expect(String(selection('search')).split('-'), `${name} ${runId}`).toContain('search_sweep');
      }
    }
  });

  it('records SEARCH_SCENE between SEARCH and TARGET_SELECTED in every run under scene_search, as policy_search does', () => {
    let scene = 0;
    for (const name of SCENARIO_NAMES) {
      const runs = new Map<string, ContractEvent[]>();
      for (const event of everyPlannedRun(name)) runs.set(String(event.run_id), [...(runs.get(String(event.run_id)) ?? []), event]);
      for (const [runId, own] of runs) {
        const config = own.find((event) => event.kind === 'run.config');
        const summary = (config?.payload as { summary?: Array<{ name: string; value: unknown }> } | undefined)?.summary ?? [];
        const words = String(summary.find((item) => item.name === 'policy.search.selection')?.value).split('-');
        const states = transitions(own);
        if (!words.includes('scene_search') || !states.includes('TARGET_SELECTED')) {
          expect(states, `${name} ${runId}`).not.toContain('SEARCH_SCENE');
          continue;
        }
        scene += 1;
        const at = states.indexOf('SEARCH_SCENE');
        expect(at, `${name} ${runId}`).toBeGreaterThan(states.indexOf('SEARCH'));
        expect(at, `${name} ${runId}`).toBeLessThan(states.indexOf('TARGET_SELECTED'));
      }
    }
    expect(scene).toBeGreaterThan(0);
  });

  describe('plays nothing until the operator presses what starts it', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    for (const name of SCENARIO_NAMES) {
      it(name, async () => {
        const run = drive(name);
        await vi.advanceTimersByTimeAsync(30_000);
        expect(run.events).toEqual([]);
        expect(await run.source.mission()).toEqual({ ok: false, reason_code: 'no_mission_recorded' });
        run.off();
      });
    }
  });
});

/* ---- the failure modes and the successes of 2026-09-26 ---------------------------------------------------------- */

describe('the times a rehearsal records (spec rule 5.4, section 6.7)', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('stamps every event it serves with a UTC wall time when it records it, as the runtime does', async () => {
    const run = drive('far-block-success');
    await run.source.missionStart();
    await play(run, 1);
    expect(run.events.length).toBeGreaterThan(10);
    for (const event of run.events) expect(event.t_wall_utc).toMatch(/^2026-09-27T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    const times = run.events.map((event) => Date.parse(event.t_wall_utc ?? ''));
    expect([...times].sort((a, b) => a - b)).toEqual(times);
    run.off();
  });

  it("lists each finished run with its recorded start and finish, from the run's own records", async () => {
    const run = drive('far-block-success');
    await run.source.missionStart();
    await play(run, 1);
    const history = await run.source.history();
    if (!history.ok) throw new Error('no history');
    const entry = history.entries[0];
    const started = run.events.find((event) => event.kind === 'run.started');
    const ended = run.events.find((event) => event.kind === 'run.finished');
    expect(entry?.started_wall_utc).toMatch(/Z$/);
    expect(entry?.finished_wall_utc).toMatch(/Z$/);
    expect(entry?.started_wall_utc).toBe(started?.t_wall_utc);
    expect(entry?.finished_wall_utc).toBe(ended?.t_wall_utc);
    run.off();
  });
});

describe('the scenarios of 2026-09-26', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("near-block-grasp-miss: the head's controller refuses the block of 19:19 at SEARCH, before any motion, and the mission holds", async () => {
    const run = drive('near-block-grasp-miss');
    expect((await run.source.missionStart()).kind).toBe('started');
    await play(run, 1);
    // policy_align.refuse_pickup_band, after the selection and before PREGRASP: nothing is commanded.
    expect(transitions(run.events)).toEqual(['SEARCH', 'TARGET_SELECTED']);
    expect(run.events.some((event) => event.kind === 'action.planned' || event.kind === 'action.started')).toBe(false);
    expect(stateLines(run.events, 'SEARCH')[0]).toMatchObject({ pose: { shoulder_pan: -8.7 } });
    expect(predicates(run.events, 'pregrasp_cube_within_reach')).toEqual([expect.objectContaining({ value: false, measured: -44.5, threshold: -13.0 })]);
    const contact = perceptions(run.events, 'cube-recognizer')[0];
    expect([measurement(contact?.measurements, 'contact_x'), measurement(contact?.measurements, 'contact_y')]).toEqual([495, 696]);
    const [end] = finished(run.events);
    expect(end).toMatchObject({ outcome: 'failed', exit_status: 5, reason_code: 'search_cube_inside_pregrasp_reach', phase: 'TARGET_SELECTED' });
    expect(end?.failure?.detail).toBe(
      'SEARCH: the block stands 31.5 mm nearer than the pregrasp can reach (coarse cube_out -44.5 mm, the pregrasp clamps at -13); move it at least 57.5 mm away from the robot, or let the base reverse (T6 step 2b); nothing was picked up',
    );
    expect(end?.failure?.measurements).toEqual([
      { name: 'cube_out_mm', value: -44.5, unit: 'mm' },
      { name: 'min_cube_out_mm', value: -13.0, unit: 'mm' },
      { name: 'move_block_at_least_mm', value: 57.5, unit: 'mm' },
    ]);
    expect(end?.failure?.observation_ids).toEqual(contact?.observation_ids);
    await noteMission(run);
    // An envelope refusal before any motion is operator_needed: the mission holds, and the retry is "move the block, then retry".
    expect(run.missionStates).toEqual(['RUNNING', 'WAITING_FOR_CAPABILITY']);
    const held = await run.source.mission();
    if (!held.ok) throw new Error('no mission');
    expect(held.mission.waiting_reason).toBe(OPERATOR_DECISION);
    expect(held.mission.hold).toMatchObject({ attempt: 1, outcome_class: 'operator_needed', reason_code: 'search_cube_inside_pregrasp_reach', phase: 'TARGET_SELECTED', budget_left: 1, retry_requested: false });
    expect(held.mission.hold?.stop_state).toEqual({ holding: false, base_ahead: false, moved: false });
    expect(held.mission.hold?.next_actions).toEqual(['move_block_then_retry', 'stop']);
    expect(held.mission.next_actions).toEqual(['move_block_then_retry', 'stop']);
    // A rehearsal moves no block: the park runs, the fresh attempt is refused the same way, and the budget is spent.
    expect((await run.source.missionRetry()).kind).toBe('started');
    await play(run, 3);
    expect(finished(run.events).map((item) => [item.outcome, item.reason_code ?? null])).toEqual([
      ['failed', 'search_cube_inside_pregrasp_reach'],
      ['unknown', null],
      ['failed', 'search_cube_inside_pregrasp_reach'],
    ]);
    await noteMission(run);
    const spent = await run.source.mission();
    if (!spent.ok) throw new Error('no mission');
    expect(spent.mission.hold).toMatchObject({ attempt: 2, budget_left: 0, next_actions: ['stop'], park_run_id: spent.mission.targets[0]?.runs[1] });
    expect(await run.source.missionRetry()).toMatchObject({ kind: 'refused', reason_code: 'mission_retry_not_offered' });
    run.off();
  });

  it('carries the evidence scalar names and the outline fits the recorded runs carry', async () => {
    const scalars = (events: ContractEvent[], name: string): string[] =>
      ((events.find((event) => event.kind === 'predicate.evaluated' && (event.payload as { predicate: string }).predicate === name)?.payload as { evidence: Array<{ type: string; scalar_name: string | null }> } | undefined)?.evidence ?? [])
        .filter((item) => item.type === 'scalar')
        .map((item) => String(item.scalar_name));
    const near = drive('near-block-grasp-miss');
    await near.source.missionStart();
    await play(near, 1);
    expect(scalars(near.events, 'pregrasp_pan_within_return_bound')).toEqual(['coarse_pan_deg', 'home_pan_deg']);
    expect(scalars(near.events, 'pregrasp_cube_within_reach')).toEqual(['coarse_cube_out_mm']);
    // m8c5f9e9's SEARCH frame fitted the outline.
    const outline = perceptions(near.events, 'pad-outline-fit');
    expect(outline.map((item) => [item.result, measurement(item.measurements, 'boundary_rms'), measurement(item.measurements, 'visible_fraction')])).toEqual([['detected', 1.39, 0.757]]);
    near.off();
    const far = drive('far-left-yawed-success');
    await far.source.missionStart();
    await play(far, 1);
    expect(scalars(far.events, 'trim_pan_within_return_bound')).toEqual(['commanded_pan_deg', 'home_pan_deg']);
    expect(scalars(far.events, 'closed_on_something')).toEqual(['gripper_readback', 'gripper_latch_value', 'gripper_empty_latch_margin', 'gripper_holding_band_max']);
    expect(scalars(far.events, 'object_secured')).toEqual(['retention_score', 'constant.retention_min_score.empirical']);
    expect(scalars(far.events, 'placement_plan')).toEqual(['anchor_margin_mm', 'grasp_out_mm', 'constant.visual_anchor_uncertainty_mm.empirical', 'constant.fk_travel_overstatement.empirical', 'constant.arm_loaded_reach_out_mm.empirical']);
    expect(scalars(far.events, 'base_measured')).toEqual(['pad_cross_distortion_mm', 'advance_mm', 'lateral_mm', 'pulse_duration_s']);
    expect(scalars(far.events, 'base_stop_confirmed_by_primitive')).toEqual(['base_velocity_reads', 'base_velocity_fresh_reads']);
    expect(scalars(far.events, 'release_margin')).toEqual(['fk_travel_from_grasp_mm', 'base_advance_measured_mm', 'constant.visual_anchor_uncertainty_mm.empirical', 'constant.fk_travel_overstatement.empirical', 'constant.arm_loaded_reach_out_mm.empirical']);
    far.off();
  });

  it('far-block-success: 108 mm planned, three measured pulses, placed 47.5 mm from the edge, returned', async () => {
    const run = drive('far-block-success');
    await run.source.missionStart();
    await play(run, 1);
    expect(stateLines(run.events, 'PLACEMENT_PLAN')[0]).toMatchObject({ mode: 'BASE_ADVANCE', base_advance_mm: 108.0, anchor_margin_mm: -48.5 });
    expect(stateLines(run.events, 'BASE_MEASURED').filter((line) => (line['vx_mps'] as number) > 0).map((line) => line['advance_mm'])).toEqual([26.6, 40.1, 39.7]);
    expect(stateLines(run.events, 'BASE_ADVANCE').map((line) => line['axis_pad'])).toEqual([[-0.042, 0.999], [-0.01, 1.0], [0.047, 0.999]]);
    expect(stateLines(run.events, 'RELEASE_ADMISSION')[0]).toMatchObject({ admitted: true, worst_case_centre_margin_mm: 43.8 });
    expect(stateLines(run.events, 'PLACED')[0]).toMatchObject({ value: true, centre_to_edge_mm: 47.5 });
    expect(stateLines(run.events, 'BASE_RETURNED')[0]).toMatchObject({ value: true, remaining_along_axis_mm: 2.9, sideways_mm: -2.2 });
    expect(predicates(run.events, 'object_secured')[0]?.measured).toBeCloseTo(0.915, 3);
    const [end] = finished(run.events);
    expect(end?.outcome).toBe('placed_new');
    expect(end?.reason_code ?? null).toBeNull();
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'COMPLETE']);
    const complete = await run.source.mission();
    if (!complete.ok) throw new Error('no mission');
    expect(complete.mission.targets[0]?.state).toBe('PLACED');
    expect(complete.mission.targets[0]?.measured_mode).toBe('BASE_ADVANCE');
    run.off();
  });

  it('far-block-success: a held frame with a valid pad frame and no cube placed reads not_detected, as the recorded run does', async () => {
    const run = drive('far-block-success');
    await run.source.missionStart();
    await play(run, 1);
    // The controller: 'detected' if placed else ('not_detected' if pad_frame_valid else 'error'). The recorded mb3c1048
    // HOLD_OBSERVE and pulse frames read not_detected with 74 to 107 landmarks.
    const byFrame = (suffix: string): string | undefined => perceptions(run.events, 'cube-in-pad-frame').find((item) => item.observation_ids[0]?.endsWith(suffix))?.result;
    expect(byFrame('-hold')).toBe('not_detected');
    expect(byFrame('-pulse-1')).toBe('not_detected');
    // No pad frame at all (the close frames see no print): the layer never ran, and the controller says error.
    expect(byFrame('-clearance')).toBe('error');
    expect(byFrame('-search')).toBe('detected');
    run.off();
  });

  it('far-left-yawed-success: one pan trim, three pulses, placed 51.1 mm, the 9.1 mm sideways return', async () => {
    const run = drive('far-left-yawed-success');
    await run.source.missionStart();
    await play(run, 1);
    expect(stateLines(run.events, 'PREGRASP')[0]).toMatchObject({ coarse: { pan_deg: -30.0, forward_mm: 47.1, cube_out_mm: 34.1 } });
    expect(stateLines(run.events, 'ALIGN').map((line) => [line['lateral_mm'], line['aligned']])).toEqual([[13.7, false], [4.6, true], [5.0, true]]);
    expect(stateLines(run.events, 'BASE_MEASURED').filter((line) => (line['vx_mps'] as number) > 0).map((line) => line['advance_mm'])).toEqual([23.5, 42.1, 14.9]);
    expect(stateLines(run.events, 'PLACED')[0]).toMatchObject({ centre_to_edge_mm: 51.1 });
    expect(stateLines(run.events, 'BASE_RETURNED')[0]).toMatchObject({ remaining_along_axis_mm: -0.1, sideways_mm: -9.1, tolerance_mm: 10.0 });
    const [end] = finished(run.events);
    expect(end?.outcome).toBe('placed_new');
    expect(end?.automatic_retries).toBe(1);
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'COMPLETE']);
    run.off();
  });

  it('not-placeable-side: refused at SEARCH before motion, with the side limit the head records', async () => {
    const run = drive('not-placeable-side');
    await run.source.missionStart();
    await play(run, 1);
    expect(transitions(run.events)).toEqual(['SEARCH']);
    expect(run.events.some((event) => event.kind === 'action.planned')).toBe(false);
    const block = perceptions(run.events, 'cube-in-pad-frame')[0];
    expect([measurement(block?.measurements, 'cube_center_pad_x'), measurement(block?.measurements, 'cube_center_pad_y')]).toEqual([0.163, -0.21]);
    const [end] = finished(run.events);
    // A SEARCH binding refusal is a CycleFailed: exit 5, which the controller always records as 'failed'.
    expect(end?.outcome).toBe('failed');
    expect(end?.exit_status).toBe(5);
    expect(end?.reason_code).toBe('mission_binding_candidate_not_placeable');
    expect(end?.phase).toBe('SEARCH');
    // THE HEAD'S RECORD OF THE REFUSAL (target_binding.bind_targets with the SeenCube explanation, d801068): the explanation's
    // values sorted by name, then the counts, in that order; its sentence and hint in the detail. The values are track P's
    // explain_refusal for start 3 on the axis of start 1 with the run's side drift allowance.
    expect(end?.failure?.measurements).toEqual([
      { name: 'along_budget_deficit_mm', value: 1.6, unit: 'mm' },
      { name: 'axis_advance_needed_unbounded_mm', value: 121.58209433647478, unit: 'mm' },
      { name: 'binding_edge', value: 'right', unit: null },
      { name: 'constraint', value: 'side_edge', unit: null },
      { name: 'deficit_mm', value: 9.4, unit: 'mm' },
      { name: 'explanation', value: 'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance', unit: null },
      { name: 'hint', value: 'move the block 10 mm to the left as seen from the robot and 3 mm toward the pad', unit: null },
      { name: 'hint_places_block', value: true, unit: null },
      { name: 'model_admits_under', value: 'projected+reach_after_advance', unit: null },
      { name: 'required_axis_margin_mm', value: 58.96051224213831, unit: 'mm' },
      { name: 'required_side_margin_mm', value: 58.96051224213831, unit: 'mm' },
      { name: 'side_margin_mm', value: 44.5, unit: 'mm' },
      { name: 'candidates', value: 1, unit: null },
      { name: 'bound_earlier', value: 0, unit: null },
      { name: 'cubes_seen', value: 1, unit: null },
      { name: 'placed_cubes', value: 0, unit: null },
    ]);
    expect(end?.failure?.detail).toBe(
      'BINDING: the one cube for target cube has no bounded placement from this SEARCH (neither the arm alone nor the bounded base advance reaches the pad from it): the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance; move the block 10 mm to the left as seen from the robot and 3 mm toward the pad; nothing was picked up',
    );
    const notes = run.events.filter((event) => event.kind === 'diagnostic.text').map((event) => (event.payload as { text: string }).text);
    expect(notes.some((text) => text.startsWith('synthetic script:') && text.includes('explain_refusal') && text.includes('start 1'))).toBe(true);
    // The binding refusal holds the mission for the operator, with nothing moved and no Retry after it
    // (mission_execution.NO_RETRY_CODES): the hold offers only Stop, in every configuration.
    await noteMission(run);
    const held = await run.source.mission();
    if (!held.ok) throw new Error('no mission');
    expect(held.mission).toMatchObject({ state: HOLD_FOR_OPERATOR_STATE, waiting_reason: OPERATOR_DECISION });
    expect(held.mission.next_actions).toEqual(['stop']);
    expect(held.mission.hold?.next_actions).toEqual(['stop']);
    expect((held.mission.targets[0] as { attempts?: ScenarioAttemptRecord[] }).attempts?.map((item) => [item.outcome_class, item.stop_state.moved])).toEqual([['operator_needed', false]]);
    expect(await run.source.missionRetry()).toMatchObject({ kind: 'refused' });
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', HOLD_FOR_OPERATOR_STATE]);
    run.off();
  });

  it('not-placeable-budget: refused at SEARCH, 131.6 mm needed against the 120 mm budget', async () => {
    const run = drive('not-placeable-budget');
    await run.source.missionStart();
    await play(run, 1);
    const block = perceptions(run.events, 'cube-in-pad-frame')[0];
    expect([measurement(block?.measurements, 'cube_center_pad_x'), measurement(block?.measurements, 'cube_center_pad_y')]).toEqual([0.064, -0.22]);
    expect(transitions(run.events)).toEqual(['SEARCH']);
    const [end] = finished(run.events);
    expect(end?.outcome).toBe('failed');
    expect(end?.exit_status).toBe(5);
    expect(end?.phase).toBe('SEARCH');
    expect(end?.reason_code).toBe('mission_binding_candidate_not_placeable');
    expect(end?.failure?.measurements).toEqual([
      { name: 'axis_advance_needed_unbounded_mm', value: 131.59210434648477, unit: 'mm' },
      { name: 'constraint', value: 'along_budget', unit: null },
      { name: 'deficit_mm', value: 11.6, unit: 'mm' },
      { name: 'explanation', value: 'the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm', unit: null },
      { name: 'hint', value: 'move the block 12 mm toward the pad', unit: null },
      { name: 'hint_places_block', value: true, unit: null },
      { name: 'model_admits_under', value: 'projected+reach_after_advance', unit: null },
      { name: 'required_axis_margin_mm', value: 58.96051224213831, unit: 'mm' },
      { name: 'required_side_margin_mm', value: 58.96051224213831, unit: 'mm' },
      { name: 'side_margin_mm', value: 143.5, unit: 'mm' },
      { name: 'candidates', value: 1, unit: null },
      { name: 'bound_earlier', value: 0, unit: null },
      { name: 'cubes_seen', value: 1, unit: null },
      { name: 'placed_cubes', value: 0, unit: null },
    ]);
    expect(end?.failure?.detail).toContain('the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm; move the block 12 mm toward the pad; nothing was picked up');
    expect(run.events.some((event) => event.kind === 'action.planned')).toBe(false);
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', HOLD_FOR_OPERATOR_STATE]);
    const held = await run.source.mission();
    expect(held.ok && held.mission.hold?.next_actions).toEqual(['stop']);
    run.off();
  });

  // THE TWO ALIGN LOSSES. Their full records are on the robot; the first-hand record of each gives the block position,
  // the PREGRASP coarse values, every ALIGN attempt, the reason code, the controller's sentence and the attempt. The
  // recognizer's words are exactly its own note and nothing else; any cause beyond them is the console's inference and
  // is labelled so, never put in the recognizer's voice.
  const RECOGNIZER_NOTE = 'no detector proposal: nothing to verify';
  const SENTENCE = `ALIGN: the cube is not detected in the pregrasp view. The recognizer's own reason: ${RECOGNIZER_NOTE}`;
  const SEVEN = ['bbox_x0', 'bbox_y0', 'bbox_x1', 'bbox_y1', 'contact_x', 'contact_y', 'contact_available'];
  const notesOf = (events: ContractEvent[]): string[] => events.filter((event) => event.kind === 'diagnostic.text').map((event) => (event.payload as { text: string }).text);

  it('align-no-proposal (m76fe86c): one trim, accepted at radial +14.5, descended, no detector proposal at attempt 2', async () => {
    const run = drive('align-no-proposal');
    await run.source.missionStart();
    await play(run, 1);
    expect(stateLines(run.events, 'PREGRASP')[0]).toMatchObject({ coarse: { pan_deg: 11.9, forward_mm: 9.6, cube_out_mm: -3.4 } });
    expect(stateLines(run.events, 'ALIGN').map((line) => [line['attempt'], line['lateral_mm'], line['radial_mm'], line['aligned']])).toEqual([
      [0, 9.2, 13.9, false],
      [1, 3.4, 14.5, true],
    ]);
    expect(stateLines(run.events, 'TARGET_SELECTED')[0]).toMatchObject({ seen_pad_m: [0.075, -0.205] });
    const planned = run.events.filter((event) => event.kind === 'action.planned').map((event) => JSON.stringify(event.payload));
    expect(planned.some((text) => text.includes('ALIGN pan trim'))).toBe(true);
    expect(planned.some((text) => text.includes('ALIGN non-contact descent'))).toBe(true);
    const lost = perceptions(run.events, 'cube-recognizer').at(-1);
    expect(lost?.result).toBe('not_detected');
    expect(lost?.measurements.map((item) => item.name)).toEqual(SEVEN);
    expect(measurement(lost?.measurements, 'bbox_x0')).toBeNull();
    const [end] = finished(run.events);
    expect(end?.outcome).toBe('failed');
    expect(end?.exit_status).toBe(5);
    expect(end?.reason_code).toBe('align_cube_not_detected');
    expect(end?.phase).toBe('ALIGN');
    expect(end?.automatic_retries).toBe(1);
    expect(end?.failure?.detail).toBe(SENTENCE);
    expect(end?.failure?.measurements).toEqual([
      { name: 'attempt', value: 2, unit: null },
      { name: 'recognizer_reason', value: RECOGNIZER_NOTE, unit: null },
    ]);
    expect(end?.failure?.observation_ids).toEqual([lost?.observation_ids[0]]);
    // What is invented says so: the frames, the tool strings and the trim's measured pan.
    expect(notesOf(run.events).some((text) => text.startsWith('synthetic script:') && text.includes('m76fe86c') && text.includes('invented'))).toBe(true);
    // What is not first-hand is named: the SEARCH pose and TARGET_SELECTED's mode and counts are assumed, and PREGRASP's
    // cube_inside_pad_mm is derived from the block position with the controller's own functions.
    const own = notesOf(run.events).find((text) => text.includes('m76fe86c')) ?? '';
    expect(own).toContain('SEARCH pose');
    expect(own).toContain('TARGET_SELECTED');
    expect(own).toContain('cube_inside_pad_mm');
    expect(own).toContain('derived');
    await expectTheHeadsHold(run);
    run.off();
  });

  it('align-clipped-top (m66738ff): accepted at radial +12.4, descended, no detector proposal; the top edge is the console inference', async () => {
    const run = drive('align-clipped-top');
    await run.source.missionStart();
    await play(run, 1);
    expect(stateLines(run.events, 'PREGRASP')[0]).toMatchObject({ coarse: { pan_deg: 13.2, forward_mm: 41.0, cube_out_mm: 28.0 } });
    expect(stateLines(run.events, 'ALIGN').map((line) => [line['attempt'], line['lateral_mm'], line['radial_mm'], line['aligned']])).toEqual([[0, 1.4, 12.4, true]]);
    expect(stateLines(run.events, 'TARGET_SELECTED')[0]).toMatchObject({ seen_pad_m: [0.082, -0.179] });
    // align_cube_not_detected means the recognizer reported NO detection: a detected box would have gone on to
    // alignment_from_bbox. So the frame draws no block, and nothing on the recognizer's result names a border.
    const lost = perceptions(run.events, 'cube-recognizer').at(-1);
    expect(lost?.result).toBe('not_detected');
    expect(lost?.measurements.map((item) => item.name)).toEqual(SEVEN);
    const lostId = lost?.observation_ids[0];
    expect(lostId).toBeDefined();
    const [end] = finished(run.events);
    expect(end?.outcome).toBe('failed');
    expect(end?.reason_code).toBe('align_cube_not_detected');
    expect(end?.automatic_retries).toBe(0);
    expect(end?.failure?.detail).toBe(SENTENCE);
    expect(end?.failure?.measurements).toEqual([
      { name: 'attempt', value: 1, unit: null },
      { name: 'recognizer_reason', value: RECOGNIZER_NOTE, unit: null },
    ]);
    expect(end?.failure?.observation_ids).toEqual([lostId]);
    // Nothing in the recognizer's voice beyond its note: no border, no blur, no shadow in the reason or the sentence.
    for (const word of ['top', 'clipped', 'blur', 'shadow']) {
      expect(end?.failure?.detail).not.toContain(word);
    }
    const inference = notesOf(run.events).filter((text) => text.includes('top edge'));
    expect(inference.length).toBeGreaterThan(0);
    for (const text of inference) {
      expect(text.startsWith('synthetic script: console inference, not the recognizer')).toBe(true);
    }
    expect(inference.join(' ')).toContain('draws no block');
    await expectTheHeadsHold(run);
    run.off();
  });

  it('observe-landmarks-only: Look finds the pad by its print (97 landmarks) while the outline fit fails', async () => {
    const run = drive('observe-landmarks-only');
    const refused = await run.source.missionStart();
    expect(refused.kind).toBe('refused');
    const submitted = await run.source.submit('look-1', command('observe'));
    expect(submitted.kind).toBe('result');
    await play(run, 1);
    expect(transitions(run.events)).toEqual([]);
    expect(perceptions(run.events, 'pad-outline-fit')[0]?.result).toBe('not_detected');
    const landmarks = perceptions(run.events, 'pad-print-landmarks')[0];
    expect(landmarks?.result).toBe('detected');
    expect(measurement(landmarks?.measurements, 'landmark_inliers')).toBe(97);
    expect(measurement(landmarks?.measurements, 'pad_frame_valid')).toBe(true);
    const block = perceptions(run.events, 'cube-in-pad-frame')[0];
    expect([measurement(block?.measurements, 'cube_center_pad_x'), measurement(block?.measurements, 'cube_center_pad_y')]).toEqual([-0.0929, -0.1694]);
    const started = run.events.find((event) => event.kind === 'run.started');
    expect((started?.payload as { command_type: string }).command_type).toBe('observe');
    expect(finished(run.events)[0]?.outcome).toBe('unknown');
    run.off();
  });

  it('preview-stall: the preview yields to decision captures, then stalls with its seq frozen', async () => {
    const run = drive('preview-stall');
    await vi.advanceTimersByTimeAsync(2_000);
    const before = run.previews.at(-1);
    expect(before?.state).toBe('connected');
    expect(before?.available).toBe(true);
    await run.source.missionStart();
    await play(run, 1);
    expect(run.previews.some((sample) => (sample.skipped_for_mission ?? 0) > 0)).toBe(true);
    const stalled = run.previews.filter((sample) => sample.state === 'stalled');
    expect(stalled.length).toBeGreaterThan(0);
    const frozen = new Set(stalled.map((sample) => sample.seq));
    expect(frozen.size).toBe(1);
    expect([...frozen][0]).not.toBeNull();
    // The runtime's stalled slot (sources.py, _freshen and slot): unavailable, the runtime's reason, the last seq and
    // how long ago it arrived kept, and every measured field null.
    for (const sample of stalled) {
      expect(sample.available).toBe(false);
      expect(sample.reason).toBe('no_sample_within_window');
      expect(sample.measured_fps).toBeNull();
      expect(sample.frame).toBeNull();
      expect(sample.capture_clock).toBeNull();
      expect(sample.service_latency_ms).toBeNull();
      expect(sample.gaps).toBeNull();
      expect(sample.session_changes).toBeNull();
      expect(sample.capture_time_stalls).toBeNull();
      expect(sample.malformed).toBeNull();
      expect(sample.stream?.source_fps_measured ?? null).toBeNull();
      expect(sample.stream?.pixel_format ?? null).toBeNull();
    }
    const ages = stalled.map((sample) => sample.receive_age_ms ?? -1);
    for (let index = 1; index < ages.length; index += 1) expect(ages[index]).toBeGreaterThan(ages[index - 1] ?? 0);
    // Frames arriving with a moving capture clock: no capture-time stall is counted anywhere in this scenario.
    const connected = run.previews.filter((sample) => sample.state === 'connected');
    expect(connected.every((sample) => sample.capture_time_stalls === 0)).toBe(true);
    expect(finished(run.events)[0]?.outcome).toBe('placed_new');
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'COMPLETE']);
    run.off();
  });

  it('preview-not-configured: every preview line says the runtime was given no preview', async () => {
    const run = drive('preview-not-configured');
    await vi.advanceTimersByTimeAsync(2_000);
    expect(run.previews.length).toBeGreaterThan(0);
    expect(run.previews.every((sample) => sample.state === 'disconnected' && sample.reason === 'preview_not_configured' && sample.available === false)).toBe(true);
    // The runtime's unavailable_preview always says this (packages/demo-runtime/demo_runtime/sources.py CAPTURE_AGE_REASON).
    expect(run.previews.every((sample) => sample.capture_age_reason === 'no_clock_relation_between_source_and_laptop')).toBe(true);
    await run.source.missionStart();
    await play(run, 1);
    expect(finished(run.events)[0]?.outcome).toBe('placed_new');
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'COMPLETE']);
    run.off();
  });

  it('abort-swap-repeated: the abort-swap refused three times with the slot unchanged, then the swapped Plan observed', async () => {
    const run = drive('abort-swap-repeated');
    await run.source.missionStart();
    await play(run, 1);
    await noteMission(run);
    const candidates = await run.source.missionCandidates();
    if (!candidates.ok) throw new Error('no candidates');
    expect(candidates.candidates.map((item) => item.key)).toEqual(['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped']);
    expect(candidates.candidates.map((item) => (item as { role?: string }).role)).toEqual(['refused-demo', 'activate']);
    for (let press = 0; press < 3; press += 1) {
      const answer = await run.source.activateCandidate('mobile-demo-active-successor-abort-swap');
      expect(answer.kind).toBe('answered');
    }
    const held = await run.source.mission();
    if (!held.ok) throw new Error('no mission');
    expect(held.mission.state).toBe('WAITING_FOR_CAPABILITY');
    expect(held.mission.activations.map((item) => item.activation_state)).toEqual(['REJECTED_BY_CAKE', 'REJECTED_BY_CAKE', 'REJECTED_BY_CAKE']);
    for (const activation of held.mission.activations) expect(activation.slot_after?.generation).toBe(activation.slot_before?.generation);
    await run.source.activateCandidate('mobile-demo-swapped');
    await play(run, 2);
    expect(finished(run.events)[1]?.outcome).toBe('placed_new');
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'WAITING_FOR_CAPABILITY', 'RUNNING', 'COMPLETE']);
    run.off();
  });

  it('abort-swap-repeated: the measuring run is m31e0b20 (SEARCH, TARGET_SELECTED, mission_capability_required) and the placed run mb3c1048', async () => {
    const run = drive('abort-swap-repeated');
    await run.source.missionStart();
    await play(run, 1);
    expect(transitions(run.events)).toEqual(['SEARCH', 'TARGET_SELECTED']);
    expect(stateLines(run.events, 'SEARCH')[0]).toEqual({ pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 37.85, wrist_flex: 67.21 } });
    expect(stateLines(run.events, 'TARGET_SELECTED')[0]).toEqual({
      target_id: 'cube',
      mode: 'BASE_ADVANCE',
      seen_pad_m: [-0.0199, -0.196],
      bound_pad_m: [-0.0199, -0.196],
      distance_to_binding_mm: 0.0,
      cubes_listed: 1,
      listed_pass: 1,
      targets_bound: 1,
      candidates: 1,
      on_pad_ignored: 0,
    });
    expect(run.events.filter((event) => event.kind === 'action.planned')).toEqual([]);
    const [measuring] = finished(run.events);
    expect(measuring).toMatchObject({ outcome: 'failed', exit_status: 5, reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' });
    expect(measuring?.failure?.measurements).toEqual([
      { name: 'declared_mode', value: 'auto', unit: null },
      { name: 'measured_mode', value: 'BASE_ADVANCE', unit: null },
    ]);
    const search = perceptions(run.events, 'cube-recognizer')[0];
    expect(measuring?.failure?.observation_ids).toEqual(search?.observation_ids);
    // The recorded SEARCH frame of m31e0b20: the verified box and contact, and the block in PAD_FRAME.
    expect(search?.result).toBe('detected');
    expect([0, 1, 2, 3, 4, 5].map((index) => search?.measurements[index]?.value)).toEqual([499, 451, 596, 569, 533.8, 556.5]);
    const padded = perceptions(run.events, 'cube-in-pad-frame')[0];
    expect(padded?.result).toBe('detected');
    expect(measurement(padded?.measurements, 'cube_center_pad_y')).toBeCloseTo(-0.19603, 5);
    expect(measurement(perceptions(run.events, 'pad-print-landmarks')[0]?.measurements, 'landmark_inliers')).toBe(93);
    // The controller's own arithmetic on that frame, before the capability gate: 48.5 mm outside, 2.16 deg from home.
    expect(predicates(run.events, 'already_placed')[0]?.measured).toBeCloseTo(-48.535, 3);
    expect(predicates(run.events, 'pregrasp_pan_within_return_bound')[0]).toMatchObject({ value: true, threshold: 25.0 });
    expect(predicates(run.events, 'pregrasp_pan_within_return_bound')[0]?.measured).toBeCloseTo(2.155, 3);
    for (let press = 0; press < 2; press += 1) await run.source.activateCandidate('mobile-demo-active-successor-abort-swap');
    await run.source.activateCandidate('mobile-demo-swapped');
    await play(run, 2);
    expect(stateLines(run.events, 'PLACEMENT_PLAN')[0]).toMatchObject({ mode: 'BASE_ADVANCE', base_advance_mm: 108.0, bound_mode: 'BASE_ADVANCE' });
    expect(stateLines(run.events, 'BASE_ADVANCE').map((line) => line['needed_mm'])).toEqual([108.0, 81.0, 41.0]);
    expect(stateLines(run.events, 'BASE_RETURN').map((line) => line['remaining_mm'])).toEqual([106.6, 66.6, 26.4]);
    expect(finished(run.events)[1]?.outcome).toBe('placed_new');
    run.off();
  });

  it('bring-up-not-confirmed: Start refused until the robot is confirmed, then the mission runs', async () => {
    const run = drive('bring-up-not-confirmed');
    const robot = await run.source.robotStatus();
    if (!robot.ok) throw new Error('no robot status');
    expect(robot.status.host.supervised).toBe(true);
    expect(robot.status.motion_permission.permitted).toBe(false);
    expect(robot.status.motion_permission.reason_code).toBe('robot_session_not_confirmed');
    expect(robot.status.motion_permission.confirmable).toBe(true);
    const refused = await run.source.missionStart();
    expect(refused).toMatchObject({ kind: 'refused', reason_code: 'robot_session_not_confirmed' });
    const instance = robot.status.motion_permission.current_host_instance;
    const session = robot.status.motion_permission.current_session_uuid;
    if (instance === null || session === null) throw new Error('nothing to confirm');
    expect(await run.source.confirmRobotSession(session, instance)).toMatchObject({ kind: 'answered', ok: true, motion_permitted: true });
    const after = await run.source.robotStatus();
    if (!after.ok) throw new Error('no robot status');
    expect(after.status.motion_permission.permitted).toBe(true);
    expect(await run.source.mission()).toEqual({ ok: false, reason_code: 'no_mission_recorded' });
    expect((await run.source.missionStart()).kind).toBe('started');
    await play(run, 1);
    expect(finished(run.events)[0]?.outcome).toBe('placed_new');
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'COMPLETE']);
    run.off();
  });

  it("serves Stop as the runtime's capability list does: on while a run or a mission is carried, off with nothing to stop", async () => {
    const run = drive('far-block-success');
    const stop = async (): Promise<unknown> => (await run.source.snapshot()).capabilities.find((item) => item.id === 'stop');
    expect(await stop()).toMatchObject({ state: 'disabled', reason_code: 'no_active_run', reason: 'there is no run to stop' });
    await run.source.missionStart();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(await stop()).toMatchObject({ state: 'enabled', reason_code: null });
    await play(run, 1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(await stop()).toMatchObject({ state: 'disabled', reason_code: 'no_active_run' });
    run.off();
  });

  it("blocks every motion command it plays with the robot's refusal while motion is held; what it never plays stays refused for that", async () => {
    for (const name of ['bring-up-not-confirmed', 'host-not-supervised'] as const) {
      const run = drive(name);
      const capabilities = (await run.source.snapshot()).capabilities;
      for (const id of ['start_cycle', 'return_arm']) {
        expect(capabilities.find((item) => item.id === id), `${name} ${id}`).toMatchObject({ state: 'blocked', reason_code: 'robot_session_not_confirmed', dependency: 'robot' });
      }
      // Neither scenario plays a scan or a Look: a confirmation would not turn them on, so they are not refused for it.
      for (const id of ['scan_scene', 'observe']) {
        expect(capabilities.find((item) => item.id === id), `${name} ${id}`).toMatchObject({ state: 'blocked', reason_code: 'synthetic_source' });
      }
      run.off();
    }
    const run = drive('bring-up-not-confirmed');
    const robot = await run.source.robotStatus();
    if (!robot.ok) throw new Error('no robot status');
    const permission = robot.status.motion_permission;
    if (permission.current_session_uuid === null || permission.current_host_instance === null) throw new Error('nothing to confirm');
    await run.source.confirmRobotSession(permission.current_session_uuid, permission.current_host_instance);
    expect((await run.source.snapshot()).capabilities.find((item) => item.id === 'start_cycle')?.state).toBe('enabled');
    run.off();
  });

  it('host-not-supervised: a host runs outside Cake, nothing to confirm, Start refused', async () => {
    const run = drive('host-not-supervised');
    const robot = await run.source.robotStatus();
    if (!robot.ok) throw new Error('no robot status');
    expect(robot.status.host.supervised).toBe(false);
    expect(robot.status.host.reason_code).toBe('supervisor_child_not_running');
    // No record of 2026-09-26 carries a restart count for this: the supervisor's child spawned once and is stopped.
    const body = syntheticRobotBody('host_outside_cake', false) as { health: { items: Array<{ state_name: string; restarts: number; spawns: number; child_pid: number }> } };
    expect(body.health.items[0]).toMatchObject({ state_name: 'STOPPED', child_pid: 0, restarts: 0, spawns: 1 });
    expect(robot.status.host.readiness_state).toBe('running_normal');
    expect(robot.status.motion_permission.confirmable).toBe(false);
    expect(robot.status.motion_permission.current_host_reason_code).toBe('robot_host_not_running');
    expect(await run.source.missionStart()).toMatchObject({ kind: 'refused', reason_code: 'robot_session_not_confirmed' });
    expect(await run.source.confirmRobotSession('00000000000000000000000000000000', { slot_instance: '1', spawn_sequence: 1, pid: 1 })).toMatchObject({ kind: 'answered', ok: false, reason_code: 'robot_host_not_running' });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(run.events).toEqual([]);
    run.off();
  });
});

/* ---- the new behaviours ----------------------------------------------------------------------------------------- */

describe('the scenarios of the new behaviours', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('scene-scan: Scan scene plays five pan-only views and the fused scene, then Start picks the block in a mission of its own', async () => {
    const run = drive('scene-scan');
    expect(scenarioSpec('scene-scan')?.trigger).toBe('scan');
    // The head's mission carries cycles and parks only: the scan is the operator's own command, pressed first.
    expect((await run.source.submit('scan-1', command('scan_scene'))).kind).toBe('result');
    await play(run, 1);
    expect(await run.source.mission()).toEqual({ ok: false, reason_code: 'no_mission_recorded' });
    expect((await run.source.missionStart()).kind).toBe('started');
    await play(run, 2);
    const views = stateLines(run.events, 'SCENE_SCAN');
    expect(views.map((line) => line['view'])).toEqual(['home', 'minus_15', 'home', 'plus_15', 'home']);
    expect(views.map((line) => line['pan_deg'])).toEqual([-9.67, -24.67, -9.67, 5.33, -9.67]);
    expect(perceptions(run.events, 'scene-view')).toHaveLength(5);
    const pad = perceptions(run.events, 'scene-pad');
    expect(pad).toHaveLength(1);
    expect(measurement(pad[0]?.measurements, 'fit_accepted')).toBe(true);
    expect(measurement(pad[0]?.measurements, 'axis_x')).toBe(-0.0125);
    expect(measurement(pad[0]?.measurements, 'axis_y')).toBe(-0.4132);
    const blocks = perceptions(run.events, 'scene-block');
    expect(blocks.map((block) => measurement(block.measurements, 'class'))).toEqual(['BASE_ADVANCE', 'OUTSIDE_PAN_BAND']);
    expect(measurement(blocks[1]?.measurements, 'inferred')).toBe(false);
    expect(measurement(blocks[1]?.measurements, 'pan_inferred')).toBe(true);
    const [scan, cycle] = finished(run.events);
    expect(scan?.outcome).toBe('unknown');
    expect(cycle?.outcome).toBe('placed_new');
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', 'COMPLETE']);
    const complete = await run.source.mission();
    if (!complete.ok) throw new Error('no mission');
    const begun = run.events.filter((event) => event.kind === 'run.started');
    expect(complete.mission.targets[0]?.runs).toEqual([begun[1]?.run_id]);
    run.off();
  });

  it('scene-scan: the scan command alone plays the scan and starts no mission', async () => {
    const run = drive('scene-scan');
    const submitted = await run.source.submit('scan-1', command('scan_scene'));
    expect(submitted.kind).toBe('result');
    await play(run, 1);
    expect(stateLines(run.events, 'SCENE_SCAN')).toHaveLength(5);
    // The run names the contract's scan command, so a completed scan is read as a scan and never as a failed cycle.
    const begun = run.events.filter((event) => event.kind === 'run.started').map((event) => (event.payload as { command_type: string | null }).command_type);
    expect(begun).toEqual(['scan_scene']);
    expect(await run.source.mission()).toEqual({ ok: false, reason_code: 'no_mission_recorded' });
    run.off();
  });

  it("Return arm, after a Stop at the hold, plays the runtime's --do exit as a run of its own and changes no mission", async () => {
    const run = drive('hold-then-retry-placed');
    await run.source.missionStart();
    await play(run, 1);
    expect((await run.source.missionStop()).kind).toBe('answered');
    const submitted = await run.source.submit('back-1', command('return_arm'));
    expect(submitted).toMatchObject({ kind: 'result', result: { status: 'running', reason_code: null } });
    await play(run, 2);
    const begun = run.events.filter((event) => event.kind === 'run.started').map((event) => (event.payload as { command_type: string | null }).command_type);
    expect(begun).toEqual(['start_cycle', 'return_arm']);
    // The exit ends as the controller's does: exit 0 and no stated outcome, which is what a finished Return arm says.
    expect(finished(run.events)[1]).toMatchObject({ outcome: 'unknown', exit_status: 0 });
    const after = await run.source.mission();
    if (!after.ok) throw new Error('no mission');
    expect(after.mission.state).toBe('STOPPED');
    run.off();
  });

  it('Return arm is refused while a mission is carried, as the runtime refuses every command but Stop', async () => {
    const run = drive('hold-then-retry-placed');
    await run.source.missionStart();
    await play(run, 1);
    const submitted = await run.source.submit('back-1', command('return_arm'));
    expect(submitted).toMatchObject({ kind: 'result', result: { status: 'rejected' } });
    run.off();
  });

  it('hold-then-retry-placed: the failure holds the mission for the operator, Retry parks and a fresh attempt places', async () => {
    const run = drive('hold-then-retry-placed');
    await run.source.missionStart();
    await play(run, 1);
    await noteMission(run);
    const held = await run.source.mission();
    if (!held.ok) throw new Error('no mission');
    // The runtime's own spelling: the WAITING state both waits share, told apart by the reason.
    expect(held.mission.state).toBe('WAITING_FOR_CAPABILITY');
    expect(HOLD_FOR_OPERATOR_STATE).toBe('WAITING_FOR_CAPABILITY');
    expect(held.mission.waiting_reason).toBe(OPERATOR_DECISION);
    expect(held.mission.hold).toMatchObject({ attempt: 1, reason_code: 'align_cube_not_detected', phase: 'ALIGN', outcome_class: RETRYABLE_CLASS, park_run_id: null });
    expect(RETRYABLE_CLASS).toBe('retryable');
    expect(held.mission.hold?.stop_state).toEqual({ holding: false, base_ahead: false, moved: true });
    expect(held.mission.hold?.next_actions).toEqual(['retry', 'stop']);
    expect(held.mission.next_actions).toEqual(['retry', 'stop']);
    expect(held.mission.hold?.budget_left).toBe(1);
    // The runtime's _hold_view, field for field: the attempt record's fields and whether a retry was asked for after it.
    expect(Object.keys(held.mission.hold ?? {}).sort()).toEqual(
      ['attempt', 'budget_left', 'measurements', 'next_actions', 'observation_ids', 'outcome_class', 'park_run_id', 'phase', 'reason_code', 'retry_requested', 'run_id', 'stop_state', 't_wall_utc', 'target_id'].sort(),
    );
    expect(held.mission.hold?.measurements).toEqual([
      { name: 'attempt', value: 1, unit: null },
      { name: 'recognizer_reason', value: 'no detector proposal: nothing to verify', unit: null },
    ]);
    expect((held.mission as { attempts?: unknown }).attempts).toBeUndefined();
    expect((held.mission.targets[0] as { attempts?: unknown[] }).attempts).toHaveLength(1);
    // WHILE THE MISSION IS CARRIED, HELD INCLUDED, THE RUNTIME TAKES ONLY STOP: every command capability is blocked with
    // the mission's reason (read_models.capabilities, block code mission_running), and a submit is refused with it.
    const heldSnapshot = await run.source.snapshot();
    for (const id of ['start_cycle', 'observe', 'return_arm', 'scan_scene']) {
      expect(heldSnapshot.capabilities.find((item) => item.id === id)).toMatchObject({ state: 'blocked', reason_code: 'mission_running', reason: 'a mission is being carried: only stop is accepted' });
    }
    for (const type of ['return_arm', 'scan_scene', 'observe', 'start_cycle']) {
      const answer = await run.source.submit(`c-${type}`, command(type));
      expect(answer.kind === 'result' ? answer.result.reason_code : null).toBe('mission_running');
    }
    // Nothing moves on its own while held.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(finished(run.events)).toHaveLength(1);
    expect((await run.source.missionRetry()).kind).toBe('started');
    await play(run, 3);
    const [, park, second] = run.events.filter((event) => event.kind === 'run.started').map((event) => event.payload as { command_type: string | null });
    expect(park?.command_type).toBe('return_arm');
    expect(second?.command_type).toBe('start_cycle');
    const ends = finished(run.events);
    expect(ends.map((end) => end.outcome)).toEqual(['failed', 'unknown', 'placed_new']);
    // The fresh attempt picks the block from another place than the one ALIGN lost it at, and a note says it was moved.
    const selected = stateLines(run.events, 'TARGET_SELECTED').map((line) => line['seen_pad_m']);
    expect(selected).toEqual([[0.082, -0.179], [-0.0936, -0.1698]]);
    const notes = run.events.filter((event) => event.kind === 'diagnostic.text').map((event) => (event.payload as { text: string }).text);
    expect(notes.some((text) => text.startsWith('synthetic script:') && text.includes('moved by hand') && text.includes('(+0.082, -0.179)') && text.includes('(-0.094, -0.170)'))).toBe(true);
    await noteMission(run);
    expect(run.missionStates).toEqual(['RUNNING', HOLD_FOR_OPERATOR_STATE, 'RUNNING', 'COMPLETE']);
    const complete = await run.source.mission();
    if (!complete.ok) throw new Error('no mission');
    expect(complete.mission.targets[0]?.runs).toHaveLength(3);
    const recorded = (complete.mission.targets[0] as { attempts?: ScenarioAttemptRecord[] }).attempts ?? [];
    expect(complete.mission.hold).toBeNull();
    expect(complete.mission.next_actions).toEqual(['start']);
    // Once the mission ended, the commands are the list's again.
    expect((await run.source.snapshot()).capabilities.find((item) => item.id === 'return_arm')?.state).toBe('enabled');
    // The park prepares the NEXT attempt (mission.target_attempt: park_run_id is None for the first); the placed attempt
    // writes no attempt record, so the park is found among the target's park runs.
    expect(recorded.map((item) => item.park_run_id)).toEqual([null]);
    expect((complete.mission.targets[0] as { park_runs?: string[] }).park_runs).toEqual([complete.mission.targets[0]?.runs[1]]);
    expect((await run.source.missionRetry()).kind).toBe('refused');
    run.off();
  });

  it("hold-live-default: the head's default LIVE hold serves only Stop, and a Retry is answered mission_retry_not_enabled", async () => {
    const run = drive('hold-live-default');
    await run.source.missionStart();
    await play(run, 1);
    await noteMission(run);
    const held = await run.source.mission();
    if (!held.ok) throw new Error('no mission');
    // What the runtime writes at the hold (mission.target_waiting) and serves with it: the state, the reason, no detail.
    expect(held.mission).toMatchObject({ state: HOLD_FOR_OPERATOR_STATE, waiting_reason: OPERATOR_DECISION, detail: null });
    expect(held.mission.hold?.next_actions).toEqual(['stop']);
    expect(held.mission.next_actions).toEqual(['stop']);
    const attempts = (held.mission.targets[0] as { attempts?: ScenarioAttemptRecord[] }).attempts ?? [];
    expect(attempts.map((item) => [item.outcome_class, item.next_actions])).toEqual([[RETRYABLE_CLASS, ['stop']]]);
    expect(await run.source.missionRetry()).toEqual({ kind: 'refused', reason_code: 'mission_retry_not_enabled', detail: null });
    expect(run.missionStates).toEqual(['RUNNING', HOLD_FOR_OPERATOR_STATE]);
    expect((await run.source.missionStop()).kind).toBe('answered');
    const stopped = await run.source.mission();
    expect(stopped.ok && stopped.mission).toMatchObject({ state: 'STOPPED', waiting_reason: OPERATOR_DECISION, hold: null, next_actions: ['return_arm', 'start'] });
    run.off();
  });

  it('hold-then-retry-placed is the switch on (mission.retry and the attended label): its hold serves Try again', async () => {
    const run = drive('hold-then-retry-placed');
    await run.source.missionStart();
    await play(run, 1);
    const held = await run.source.mission();
    expect(held.ok && held.mission.hold?.next_actions).toEqual(['retry', 'stop']);
    run.off();
  });

  it('a Stop at the hold ends the mission STOPPED and keeps the reason it was held for, as the runtime does', async () => {
    const run = drive('hold-then-retry-placed');
    await run.source.missionStart();
    await play(run, 1);
    await noteMission(run);
    expect((await run.source.missionStop()).kind).toBe('answered');
    const stopped = await run.source.mission();
    if (!stopped.ok) throw new Error('no mission');
    // mission.mission_ended writes the state only: the waiting reason and the selected target stay on the record, and the
    // hold is served only while the mission waits.
    expect(stopped.mission).toMatchObject({ state: 'STOPPED', waiting_reason: OPERATOR_DECISION, selected_target: 'cube', hold: null, stop_requested: true });
    // No run was stopped, so no attempt is added: the held attempt's state decides, and the arm had moved.
    expect(stopped.mission.next_actions).toEqual(['return_arm', 'start']);
    run.off();
  });

  it('tampered-refused-before-transaction: an integrity refusal before any transaction, then the swapped Plan observed', async () => {
    const run = drive('tampered-refused-before-transaction');
    await run.source.missionStart();
    await play(run, 1);
    const candidates = await run.source.missionCandidates();
    if (!candidates.ok) throw new Error('no candidates');
    expect(candidates.candidates.map((item) => (item as { role?: string }).role)).toContain('tampered-demo');
    const tampered = candidates.candidates.find((item) => (item as { role?: string }).role === 'tampered-demo');
    if (tampered === undefined) throw new Error('no tampered candidate');
    const answer = await run.source.activateCandidate(tampered.key);
    expect(answer).toMatchObject({ kind: 'answered', activation_state: 'REFUSED_BEFORE_TRANSACTION', transaction_phase: null });
    const held = await run.source.mission();
    if (!held.ok) throw new Error('no mission');
    expect(held.mission.state).toBe('WAITING_FOR_CAPABILITY');
    const refusal = held.mission.activations[0];
    expect(refusal?.transaction).toBeNull();
    expect(refusal?.slot_after).toEqual(refusal?.slot_before);
    expect(refusal?.detail).toContain('integrity');
    expect(refusal?.detail).not.toContain('signature refus');
    await run.source.activateCandidate('mobile-demo-swapped');
    await play(run, 2);
    expect(finished(run.events)[1]?.outcome).toBe('placed_new');
    run.off();
  });

  it('tampered-refused-before-transaction: the measuring run is m07e475e (mission_capability_required) and the placed run m2476137', async () => {
    const run = drive('tampered-refused-before-transaction');
    await run.source.missionStart();
    await play(run, 1);
    expect(transitions(run.events)).toEqual(['SEARCH', 'TARGET_SELECTED']);
    expect(stateLines(run.events, 'SEARCH')[0]).toEqual({ pose: { shoulder_pan: -9.67, shoulder_lift: -79.43, elbow_flex: 38.02, wrist_flex: 67.21 } });
    expect(stateLines(run.events, 'TARGET_SELECTED')[0]).toMatchObject({ mode: 'BASE_ADVANCE', seen_pad_m: [-0.0934, -0.1697], bound_pad_m: [-0.0934, -0.1697] });
    expect(run.events.filter((event) => event.kind === 'action.planned')).toEqual([]);
    const [measuring] = finished(run.events);
    expect(measuring).toMatchObject({ outcome: 'failed', reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' });
    const search = perceptions(run.events, 'cube-recognizer')[0];
    expect([0, 1, 2, 3, 4, 5].map((index) => search?.measurements[index]?.value)).toEqual([263, 408, 348, 491, 311.7, 483.8]);
    expect(measurement(perceptions(run.events, 'pad-print-landmarks')[0]?.measurements, 'landmark_inliers')).toBe(91);
    expect(predicates(run.events, 'already_placed')[0]?.measured).toBeCloseTo(-22.229, 3);
    await run.source.activateCandidate('mobile-demo-swapped');
    await play(run, 2);
    expect(stateLines(run.events, 'BASE_ADVANCE').map((line) => line['needed_mm'])).toEqual([82.0, 58.0, 16.0]);
    expect(stateLines(run.events, 'PLACED')[0]).toMatchObject({ centre_to_edge_mm: 51.1 });
    expect(finished(run.events)[1]?.outcome).toBe('placed_new');
    run.off();
  });

  it('undo-to-inert: the capability check applies the swapped Plan and then the starting Plan, back to the inert capsule', async () => {
    const run = drive('undo-to-inert');
    expect((await run.source.missionStart()).kind).toBe('refused');
    const first = await run.source.capabilityCheck();
    if (!first.ok) throw new Error('no check');
    // Served in the runtime's order, keys sorted; the roles travel in the candidate view track C adds.
    expect(first.state.candidates.map((item) => item.key)).toEqual(['mobile-demo-starting', 'mobile-demo-swapped']);
    expect(scenarioSpec('undo-to-inert')?.check_candidates?.map((item) => [item.key, item.role])).toEqual([
      ['mobile-demo-starting', 'undo'],
      ['mobile-demo-swapped', 'activate'],
    ]);
    const inert = first.state.current.slot?.capsule;
    const generation = Number(first.state.current.slot?.generation);
    expect(first.state.current.host_held).toBe(true);
    const session = first.state.current.session_uuid ?? '';
    const [undo, swapped] = first.state.candidates;
    if (swapped === undefined || undo === undefined) throw new Error('no candidates');
    expect((await run.source.applyCapabilityCheck(swapped.key, swapped.payload_digest ?? '', session)).kind).toBe('answered');
    const middle = await run.source.capabilityCheck();
    if (!middle.ok) throw new Error('no check');
    expect(middle.state.checks.at(-1)?.activation_state).toBe('OBSERVED');
    expect(middle.state.current.slot?.capability_active).toBe(true);
    expect(Number(middle.state.current.slot?.generation)).toBe(generation + 1);
    expect((await run.source.applyCapabilityCheck(undo.key, undo.payload_digest ?? '', session)).kind).toBe('answered');
    const last = await run.source.capabilityCheck();
    if (!last.ok) throw new Error('no check');
    const undone = last.state.checks.at(-1);
    expect(undone?.activation_state).toBe('COMPLETED_NOT_OBSERVED');
    expect(undone?.slot_after?.capsule).toBe(inert);
    expect(undone?.slot_after?.capability_active).toBe(false);
    expect(undone?.host_held_throughout).toBe(true);
    expect(Number(last.state.current.slot?.generation)).toBe(generation + 2);
    expect(run.events).toEqual([]);
    run.off();
  });
});

describe('swap-then-stopped-holding: the record of mission-f65c3cf8e99f on 2026-09-27, played offline (spec 8.2)', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') }));
  afterEach(() => vi.useRealTimers());

  async function toTheWait(): Promise<Drive> {
    const run = drive('swap-then-stopped-holding');
    expect((await run.source.missionStart()).kind).toBe('started');
    await play(run, 1);
    await noteMission(run);
    return run;
  }

  it('measures BASE_ADVANCE in run 1 and waits for the capability, as run m2fc135a did', async () => {
    const run = await toTheWait();
    expect(transitions(run.events)).toEqual(['SEARCH', 'TARGET_SELECTED']);
    const error = run.events.find((event) => event.kind === 'diagnostic.text' && (event.payload as { level: string }).level === 'error');
    expect((error?.payload as { text: string }).text).toBe(
      "synthetic script: SEARCH: this target's cube measures BASE_ADVANCE, which needs the mobile_approach capability, and this run carries none; nothing was picked up",
    );
    expect(finished(run.events)[0]).toMatchObject({ outcome: 'failed', exit_status: 5, reason_code: 'mission_capability_required', phase: 'TARGET_SELECTED' });
    const mission = await run.source.mission();
    if (!mission.ok) throw new Error('no mission');
    expect(mission.mission).toMatchObject({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' });
    run.off();
  });

  it('serves its candidates with key and digest only, as 8771 serves them', async () => {
    const run = await toTheWait();
    const served = await run.source.missionCandidates();
    if (!served.ok) throw new Error('no candidates');
    expect(served.candidates.map((item) => Object.keys(item).sort())).toEqual([
      ['key', 'payload_digest'],
      ['key', 'payload_digest'],
    ]);
    expect(served.candidates.map((item) => item.key)).toEqual(['mobile-demo-active-successor-abort-swap', 'mobile-demo-swapped']);
    run.off();
  });

  it('refuses the abort-swap with the slot unchanged at 1, and activates the swap at 2 with a new capsule, which the robot then reports', async () => {
    const run = await toTheWait();
    const before = await run.source.robotStatus();
    if (!before.ok) throw new Error('no robot');
    expect(before.status.capability).toMatchObject({ active: false, generation: 1 });
    expect(before.status.slots.items.map((item) => item.name)).toContain('mobile_approach.holder');
    await run.source.activateCandidate('mobile-demo-active-successor-abort-swap');
    await run.source.activateCandidate('mobile-demo-swapped');
    const mission = await run.source.mission();
    if (!mission.ok) throw new Error('no mission');
    const [refused, swapped] = mission.mission.activations;
    expect(refused?.activation_state).toBe('REJECTED_BY_CAKE');
    expect([refused?.slot_before?.generation, refused?.slot_after?.generation]).toEqual(['1', '1']);
    expect(refused?.slot_after?.capsule).toBe(refused?.slot_before?.capsule);
    expect(swapped?.activation_state).toBe('OBSERVED');
    expect([swapped?.slot_before?.generation, swapped?.slot_after?.generation]).toEqual(['1', '2']);
    expect(swapped?.slot_after?.capsule).not.toBe(swapped?.slot_before?.capsule);
    const after = await run.source.robotStatus();
    if (!after.ok) throw new Error('no robot');
    expect(after.status.capability).toMatchObject({ active: true, generation: 2, capsule: swapped?.slot_after?.capsule });
    run.off();
  });

  it("plays run 2 in run m6ed2dd9's shapes and ends stopped at GRASP_CLOSE on the refused CLEARANCE lift", async () => {
    const run = await toTheWait();
    await run.source.activateCandidate('mobile-demo-active-successor-abort-swap');
    await run.source.activateCandidate('mobile-demo-swapped');
    await play(run, 2);
    const second = run.events.filter((event) => event.run_id === 'synthetic-run-0002');
    expect(transitions(second)).toEqual(['SEARCH', 'TARGET_SELECTED', 'PREGRASP', 'ALIGN', 'ALIGN', 'APPROACH', 'STRADDLING', 'GRASP_CLOSE']);
    expect(stateLines(second, 'ALIGN')).toHaveLength(2);
    const straddle = second.find((event) => event.kind === 'predicate.evaluated' && (event.payload as { predicate: string }).predicate === 'straddle_far_edge_row');
    expect(straddle?.action_id).toBeNull();
    expect(straddle?.payload).toMatchObject({ value: false, measured: 333, threshold: 420, unit: 'px' });
    expect(predicates(second, 'closed_on_something')[0]).toMatchObject({ value: true, measured: 67.91, threshold: 67.4 });
    const refused = second.find((event) => event.kind === 'action.refused');
    expect(refused?.reason_code).toBe('base_not_zero_before_segment');
    const joined = second.filter((event) => event.action_id !== null && event.action_id === refused?.action_id).map((event) => event.kind);
    expect(joined).toEqual(['action.planned', 'predicate.evaluated', 'measured.state', 'action.refused']);
    const zero = second.find((event) => event.kind === 'predicate.evaluated' && event.action_id === refused?.action_id);
    expect(zero?.payload).toMatchObject({ predicate: 'base_zero', value: false, measured: 0, threshold: 3, unit: 'reads' });
    const measured = second.find((event) => event.kind === 'measured.state' && event.action_id === refused?.action_id);
    expect(measured?.payload).toMatchObject({ joints: { gripper: 68.116 }, base_velocity: { theta_dps: 1.171875 } });
    expect(finished(second)[0]).toMatchObject({ outcome: 'stopped', exit_status: 4, reason_code: 'base_not_zero_before_segment', phase: 'GRASP_CLOSE', failure: { measurements: [], observation_ids: [] } });
    run.off();
  });

  it('writes a stop it did not ask for as the runtime writes it: an attempt of class stopped, the mission FAILED', async () => {
    const run = await toTheWait();
    await run.source.activateCandidate('mobile-demo-active-successor-abort-swap');
    await run.source.activateCandidate('mobile-demo-swapped');
    await play(run, 2);
    const mission = await run.source.mission();
    if (!mission.ok) throw new Error('no mission');
    expect(mission.mission).toMatchObject({ state: 'FAILED', detail: 'run_outcome:stopped:base_not_zero_before_segment', stop_requested: false, next_actions: ['open_gripper_by_hand', 'return_arm', 'start'] });
    const target = mission.mission.targets[0] as unknown as { state: string; attempts: ScenarioAttemptRecord[] };
    expect(target.state).toBe('FAILED');
    expect(target.attempts.at(-1)).toMatchObject({
      outcome_class: 'stopped',
      reason_code: 'base_not_zero_before_segment',
      phase: 'GRASP_CLOSE',
      stop_state: { holding: true, base_ahead: false, moved: true },
      next_actions: ['open_gripper_by_hand', 'return_arm', 'start'],
      measurements: [],
      observation_ids: [],
    });
    run.off();
  });

  it('serves a signature check from an earlier runtime session', async () => {
    const run = await toTheWait();
    const answer = await run.source.signatureCheck();
    if (!answer.ok) throw new Error('no signature check');
    const check = answer.state.checks.at(-1);
    expect(check?.runtime_session).not.toBe((await run.source.snapshot()).runtime_session);
    expect(check?.t_wall_utc).toBe('2026-09-26T20:26:13.435Z');
    expect(answer.state.off_reason).not.toBeNull();
    run.off();
  });
});

describe('a stop the operator asked for, while a run of the mission is in progress', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T14:16:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('keeps the STOPPED path: the mission reads STOPPED, not FAILED', async () => {
    const run = drive('far-block-success');
    await run.source.missionStart();
    await vi.advanceTimersByTimeAsync(3000);
    await run.source.stop();
    await play(run, 1);
    const mission = await run.source.mission();
    if (!mission.ok) throw new Error('no mission');
    expect(mission.mission).toMatchObject({ state: 'STOPPED', stop_requested: true });
    run.off();
  });
});
