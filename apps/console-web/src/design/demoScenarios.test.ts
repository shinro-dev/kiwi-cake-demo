// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DEMO SCREEN, RENDERED FOR EVERY SCENARIO OF THE CATALOGUE, AT ITS START, AT ITS HOLD AND AT ITS END.
//
// Each scenario is played through the real store and the synthetic source with its operator's presses
// (`demoScenarioPlay.ts`), and the DEMO screen is rendered and read from its markup. At the three moments that matter
// (before any press, where the mission first holds, and where the scenario rests) these are checked against the
// scenario's recorded facts, which are written here and not read back out of the catalogue
// (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 4 and 8):
//
//   THE OUTCOME CARD: its state, the mission's word and the run's, each its own; the stop's reason code, its own frame
//   and a placement limit; and the runtime's step list, exactly;
//   THE OFFERED ACTIONS: every control of the screen that is on, and none that is not;
//   THE STAGES: the state of each of the five;
//   THE READINESS VERDICT, and its sentence where it names the reason;
//   THE CAKE REGION: the words it must and must not hold.
//
// And on EVERY render while the scenario plays: the card names only the run on the screen while a run is in progress; no
// stage ends badly without a cause on the card; while a mission is carried only what the runtime's lists offer is on; the
// verdict is never Ready beside an off Start or a held robot; the camera chip never says Live over a panel that says
// there is no camera. A catalogue scenario with no facts here fails the first test.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SCENARIO_CATALOGUE } from '../api/syntheticScenarios';
import type { ScenarioName } from '../api/syntheticScenarios';
import { playScenario } from './demoScenarioPlay';
import type { Played, ScreenRead, Status, Step } from './demoScenarioPlay';

/* ---- the recorded facts ------------------------------------------------------------------------------------------ */

type Stages = Record<Step, Status>;
const NONE: Stages = { find: 'not_reached', pick: 'not_reached', carry: 'not_reached', place: 'not_reached', return: 'not_reached' };
const PLACED: Stages = { find: 'entered', pick: 'verified', carry: 'entered', place: 'verified', return: 'verified' };
const FIND_FAILED: Stages = { ...NONE, find: 'failed' };
const PICK_FAILED: Stages = { ...NONE, find: 'entered', pick: 'failed' };

interface Card {
  state: 'none' | 'carried' | 'expanded' | 'collapsed';
  mission?: string;
  run?: string;
  /** The reason code the card states as the cause, or null for none. */
  cause?: string | null;
  /** The end of the frame id the run's failure names. */
  frame?: string;
  limit?: string;
  steps?: string[];
}

interface Moment {
  mission: string | null;
  waiting?: string | null;
  card: Card;
  /** The controls that are on, in the harness's order (start, stop, retry, return_arm, scan_scene, observe, candidate). */
  offered: string[];
  stages: Stages;
  readiness: string;
  readySays?: string;
  cake: string[];
  cakeNot: string[];
}

interface Facts {
  start: Moment;
  /** Null: the scenario never holds, and the test says so. */
  hold: Moment | null;
  end: Moment;
  more?: (played: Played) => void;
}

const NO_ROBOT = ['The robot has not been read'];
const NO_MISSION_CAKE = ['The mission waits for Cake', 'Refused', 'Activated'];

// Only what the rehearsal's runtime takes is on: Start, and Scan scene or Look only in the scenario that plays one. The
// other rehearsals serve both off (synthetic_source), so neither is drawn on (syntheticAdmission.test.ts).
const START: Moment = { mission: null, card: { state: 'none' }, offered: ['start'], stages: NONE, readiness: 'ready', cake: ['No Cake activation in this mission', ...NO_ROBOT], cakeNot: NO_MISSION_CAKE };

const COMPLETE: Moment = {
  mission: 'COMPLETE',
  card: { state: 'expanded', mission: 'complete', run: 'placed', cause: null, steps: ['start'] },
  offered: ['start'],
  stages: PLACED,
  readiness: 'ready',
  cake: NO_ROBOT,
  cakeNot: ['The mission waits for Cake'],
};

/** The first hold for the capability: no failure, the candidates offered. */
function capabilityHold(stages: Stages): Moment {
  return {
    mission: 'WAITING_FOR_CAPABILITY',
    waiting: 'capability_unavailable',
    card: { state: 'carried', mission: 'waiting_cake', run: 'needs_capability', cause: null, steps: ['stop'] },
    offered: ['stop', 'candidate'],
    stages,
    readiness: 'waiting_cake',
    cake: ['The mission waits for Cake', ...NO_ROBOT],
    cakeNot: [],
  };
}

/** A hold for the operator's decision after a failed attempt, with what the runtime's hold offers. */
function decisionHold(code: string, frame: string, steps: string[], stages: Stages, limit?: string): Moment {
  const retry = steps.includes('retry') || steps.includes('move_block_then_retry');
  return {
    mission: 'WAITING_FOR_CAPABILITY',
    waiting: 'operator_decision',
    card: { state: 'carried', mission: 'held_for_you', run: 'failed', cause: code, frame, steps, ...(limit === undefined ? {} : { limit }) },
    offered: retry ? ['stop', 'retry'] : ['stop'],
    stages,
    readiness: 'held_for_you',
    readySays: retry ? 'Try again' : 'The runtime offers only Stop for this hold',
    cake: NO_ROBOT,
    cakeNot: ['The mission waits for Cake'],
  };
}

const ALIGN_LOST = (frame: string, steps: string[]): Moment => decisionHold('align_cube_not_detected', frame, steps, PICK_FAILED);
const NEAR_REFUSED = (frame: string, steps: string[]): Moment => decisionHold('search_cube_inside_pregrasp_reach', frame, steps, FIND_FAILED);

/**
 * A refusal at SEARCH by the target binding: nothing moved, and the runtime holds the mission for the operator with no
 * Retry after it (mission_execution.NO_RETRY_CODES), so its hold offers only Stop, in every configuration.
 */
/** The catalogue says what the scenario does: the mission holds and the runtime offers only Stop; it does not end. */
function expectCatalogueHoldsForStop(name: ScenarioName): void {
  const shows = SCENARIO_CATALOGUE.find((spec) => spec.name === name)?.shows ?? '';
  expect(shows).toContain('the mission holds for the operator and the runtime offers only Stop');
  expect(shows).not.toContain('the mission ends');
}

function notPlaceableHold(limit: string): Moment {
  return decisionHold('mission_binding_candidate_not_placeable', '-01-search', ['stop'], FIND_FAILED, limit);
}

const SIDE_LIMIT =
  'the right edge along the reach axis is 9.4 mm short of 58.96 mm after 120 mm of advance; the along-axis budget is also short, by 1.6 mm; move the block 10 mm to the left as seen from the robot and 3 mm toward the pad';
const BUDGET_LIMIT = 'the block needs 131.6 mm of base advance 11.6 mm past the along-axis budget of 120 mm; move the block 12 mm toward the pad';

const ROBOT_SLOT = ['Live slot: mobile_approach, not active, generation 1'];

/** A scenario that begins with Look, or with the capability check: nothing is on until its own control is used. */
const LOOK_ONLY: Moment = { ...START, offered: [], readiness: 'not_ready', readySays: 'this scenario is a Look: press Observe scene, which moves nothing' };
const CHECK_ONLY: Moment = { ...START, offered: [], readiness: 'not_ready', readySays: 'this scenario is the capability slot check' };
const NOT_CONFIRMED = 'No robot resident session and host instance have been confirmed by the operator';
const OUTSIDE_CAKE = 'Nothing to confirm: no host is running under the supervisor slot (pid 0, held or degraded), so there is no host to confirm';

const FACTS: Record<ScenarioName, Facts> = {
  'one-block': {
    start: START,
    hold: capabilityHold({ ...NONE, find: 'verified' }),
    end: { ...COMPLETE, stages: { ...PLACED, find: 'verified' }, cake: ['Refused as expected, slot unchanged 3 -> 3', 'Activated, slot 3 -> 4'] },
    more: (played) => {
      expect(played.afterActivations?.cake).toContain('Refused as expected');
      // C10: the abort-swap's expected refusal reads as expected, in the ok tone; the swap as activated.
      expect(played.end.activations.map((card) => card.verdict)).toEqual(['refused_as_expected', 'activated']);
      expect(played.hold?.timelineCake).toBe('');
      expect(played.end.timelineCake).toBe('');
      // N3 (browser r2, spec 4.4): the Complete card states the placement in the very line the Scene draws, from what
      // the rehearsal's carrying run records (its placed frame at (-0.041, 0.019) m, 31.0 mm from the edge); the Scene
      // draws the block its SEARCH saw, and never says it saw nothing.
      const scene = /data-scene-placement=""[^>]*>([^<]*)</.exec(played.end.markup)?.[1] ?? null;
      expect(scene).toBe('Placed at x -41.0 mm, y 19.0 mm, its centre 31 mm from the nearest edge.');
      expect(played.end.outcome.text).toContain(scene);
      expect(played.end.text).not.toContain('Nothing seen yet');
    },
  },
  'near-block-grasp-miss': {
    start: START,
    hold: NEAR_REFUSED('-01-search', ['move_block_then_retry', 'stop']),
    end: NEAR_REFUSED('-03-search', ['stop']),
    more: (played) => {
      expect(played.hold?.outcome.text).toContain('Move the block by hand, then Try again.');
      expect(played.hold?.outcome.text).toContain('31.5 mm nearer than the pregrasp can reach');
      // Move the block, then Try again: never primary (rule 4.3).
      expect(played.hold?.primary).toEqual([]);
    },
  },
  'far-block-success': {
    start: START,
    hold: null,
    end: COMPLETE,
    // No preview source attached reads as that, and not as the unconfigured preview of preview-not-configured.
    more: (played) => {
      expect(played.end.chip).toContain('Not attached');
      expect(played.end.chip).toContain('preview_source_not_attached');
      // D7 (spec 4.4): the Complete card states the placement, in the very line the Scene draws.
      const scene = /data-scene-placement=""[^>]*>([^<]*)</.exec(played.end.markup)?.[1] ?? null;
      expect(scene).toBe('Placed at x -15.8 mm, y -100.0 mm, its centre 47.5 mm from the nearest edge.');
      expect(played.end.outcome.text).toContain(scene);
    },
  },
  'far-left-yawed-success': { start: START, hold: null, end: COMPLETE },
  'not-placeable-side': {
    start: START,
    hold: notPlaceableHold(SIDE_LIMIT),
    end: notPlaceableHold(SIDE_LIMIT),
    // The limit line names the binding limit only, never the side margin that did not bind (44.5 mm).
    more: (played) => {
      expectCatalogueHoldsForStop('not-placeable-side');
      for (const screen of [played.hold, played.end]) {
        expect(screen?.outcome.limit).toBe(`Decided by ${SIDE_LIMIT}`);
        expect(screen?.outcome.limit).not.toContain('44.5');
      }
    },
  },
  'not-placeable-budget': {
    start: START,
    hold: notPlaceableHold(BUDGET_LIMIT),
    end: notPlaceableHold(BUDGET_LIMIT),
    more: () => expectCatalogueHoldsForStop('not-placeable-budget'),
  },
  'align-no-proposal': {
    start: START,
    hold: ALIGN_LOST('-01-descent', ['retry', 'stop']),
    end: ALIGN_LOST('-03-descent', ['stop']),
    more: (played) => {
      expect(played.hold?.outcome.text).toContain('attempt 2');
      // Try again, listed first by the hold, is the one primary control (rule 4.3).
      expect(played.hold?.primary).toEqual(['retry']);
    },
  },
  'align-clipped-top': {
    start: START,
    hold: ALIGN_LOST('-01-descent', ['retry', 'stop']),
    end: ALIGN_LOST('-03-descent', ['stop']),
    more: (played) => expect(played.hold?.outcome.text).toContain('attempt 1'),
  },
  'observe-landmarks-only': {
    // Start is served off with the scenario's own refusal, and Scan scene is not played here. Look moves nothing and
    // starts no mission.
    start: LOOK_ONLY,
    hold: null,
    end: LOOK_ONLY,
  },
  'preview-stall': {
    start: START,
    hold: null,
    end: COMPLETE,
    more: (played) => {
      // The stall is said as the runtime's reason, in one word and one line, and never with an age (rule 6.8).
      const stalled = played.all.find((screen) => screen.chip.startsWith('Stalled'));
      expect(stalled?.chip).toContain('no_sample_within_window');
      for (const screen of played.all) expect(screen.chip).not.toMatch(/\bago\b/);
      // THE CATALOGUE SAYS WHAT THE REHEARSAL DRAWS (browser r2 N5): it serves no frames, so no picture is kept or drawn
      // through the gap; the wrist says Stalled with the runtime's reason, and never an age.
      for (const screen of played.all) expect(screen.markup).not.toContain('data-kept-picture');
      const shows = SCENARIO_CATALOGUE.find((spec) => spec.name === 'preview-stall')?.shows ?? '';
      expect(shows).not.toContain('the last picture this page decoded');
      expect(shows).toContain('no picture is drawn, because the rehearsal serves no frames');
      expect(shows).toContain('Stalled, with the reason no_sample_within_window');
      expect(shows).not.toContain('with its age');
    },
  },
  'preview-not-configured': {
    start: START,
    hold: null,
    end: COMPLETE,
    more: (played) => {
      expect(played.end.chip).toContain('Not configured');
      expect(played.end.chip).toContain('preview_not_configured');
    },
  },
  'abort-swap-repeated': {
    start: START,
    hold: capabilityHold({ ...NONE, find: 'entered' }),
    end: { ...COMPLETE, cake: ['Refused as expected, slot unchanged 3 -> 3', 'Activated, slot 3 -> 4'] },
    more: (played) => {
      // Every refusal keeps its own card, oldest first (T1, C2).
      expect(played.end.activations.map((card) => card.verdict)).toEqual(['refused_as_expected', 'refused_as_expected', 'refused_as_expected', 'activated']);
      expect(played.end.timelineCake).toBe('');
    },
  },
  'bring-up-not-confirmed': {
    // Before the confirmation: the verdict names the unmet prerequisite, nothing that moves is on, and the confirmation
    // is the one primary control.
    start: { ...START, offered: [], readiness: 'not_ready', readySays: NOT_CONFIRMED, cake: ROBOT_SLOT, cakeNot: NO_MISSION_CAKE },
    hold: null,
    end: { ...COMPLETE, cake: ROBOT_SLOT },
    more: (played) => expect(played.start.primary).toEqual(['robot-confirm']),
  },
  // The host runs outside the supervisor slot: motion is held and nothing can be confirmed from this page, so the
  // readiness sentence names that hold in the motion hold's own words, never "confirm the connected robot first".
  'host-not-supervised': {
    start: { ...START, offered: [], readiness: 'not_ready', readySays: OUTSIDE_CAKE, cake: ROBOT_SLOT },
    hold: null,
    end: { ...START, offered: [], readiness: 'not_ready', readySays: OUTSIDE_CAKE, cake: ROBOT_SLOT },
    more: (played) => {
      for (const screen of [played.start, played.end]) {
        expect(screen.readinessSentence).toContain('Robot motion is held.');
        expect(screen.readinessSentence.toLowerCase()).not.toContain('confirm the connected robot');
        expect(screen.markup).not.toContain('data-control="robot-confirm"');
        expect(screen.startOff).toBe(true);
      }
    },
  },
  // Scan scene on its own, then Start: the head's mission carries only the cycle. The one scenario that plays a scan.
  'scene-scan': { start: { ...START, offered: ['start', 'scan_scene'] }, hold: null, end: { ...COMPLETE, offered: ['start', 'scan_scene'] } },
  'hold-then-retry-placed': {
    start: START,
    hold: ALIGN_LOST('-01-descent', ['retry', 'stop']),
    end: COMPLETE,
    more: (played) => expect(played.receipts.some((text) => text.includes('The runtime took the retry'))).toBe(true),
  },
  // THE HEAD'S DEFAULT LIVE HOLD: mission.retry ships off, so the runtime serves the hold with ['stop'] and answers a Retry
  // mission_retry_not_enabled. Only Stop is on, and nothing on the screen offers or describes a Try again.
  'hold-live-default': {
    start: START,
    hold: ALIGN_LOST('-01-descent', ['stop']),
    end: ALIGN_LOST('-01-descent', ['stop']),
    more: (played) => {
      expect(played.hold?.holdOffers).toEqual(['stop']);
      expect(played.hold?.text).not.toContain('Try again');
      expect(played.hold?.markup).not.toContain('data-control="retry"');
      expect(played.end.holdOffers).toEqual(['stop']);
    },
  },
  'tampered-refused-before-transaction': {
    start: START,
    hold: capabilityHold({ ...NONE, find: 'entered' }),
    end: { ...COMPLETE, cake: ['Refused before any transaction, as expected', 'Activated, slot 3 -> 4'] },
    more: (played) => {
      expect(played.afterActivations?.cake).toContain('integrity refusal');
      expect(played.end.activations.map((card) => card.verdict)).toEqual(['refused_before_as_expected', 'activated']);
      expect(played.end.timelineCake).toBe('');
    },
  },
  'undo-to-inert': {
    // Start is served off with the scenario's own refusal: the capability check is what it plays.
    start: { ...CHECK_ONLY, cake: ['No Cake activation in this mission', 'Live slot: mobile_approach, not active, generation 1'] },
    hold: null,
    // The capability check moves nothing and runs no cycle, and its records are Diagnostics' (Cake), not the main screen's
    // (C1); the live slot footer names the check that set it.
    end: {
      ...CHECK_ONLY,
      cake: ['Live slot: mobile_approach, not active, generation 3', 'Set by the slot check of mobile-demo-starting at'],
      cakeNot: [...NO_MISSION_CAKE, 'Last check', 'as expected for the undo role'],
    },
  },
  // THE RECORD OF 2026-09-27 ON 8771 (spec 8.2): the capability wait, the abort-swap refused 1 -> 1 with no expectation
  // served, the swap activated 1 -> 2, then run 2 stopped by the controller at GRASP_CLOSE holding the block.
  'swap-then-stopped-holding': {
    start: { ...START, cake: ['No Cake activation in this mission', 'Live slot: mobile_approach, not active, generation 1'] },
    hold: { ...capabilityHold({ ...NONE, find: 'entered' }), cake: ['The mission waits for Cake', 'Live slot: mobile_approach, not active, generation 1'] },
    end: {
      mission: 'FAILED',
      card: { state: 'expanded', mission: 'failed', run: 'stopped_by_controller', cause: 'base_not_zero_before_segment', steps: ['open_gripper_by_hand', 'return_arm', 'start'] },
      offered: ['start', 'return_arm'],
      stages: { ...NONE, find: 'entered', pick: 'stopped_by_controller' },
      readiness: 'steps_first',
      readySays: 'Open the gripper by hand and take the block out.',
      // The candidates are served without an expectation, and the list was read: the refusal's note is said (spec 5.5).
      cake: ['Cake refused the successor, slot unchanged 1 -> 1', 'Activated, slot 1 -> 2', 'Set by the activation of mobile-demo-swapped at', 'This runtime serves no expected outcome for this Plan.'],
      cakeNot: ['The mission waits for Cake'],
    },
    more: (played) => {
      const end = played.end;
      // C3: the mission and its run each in its own word, the stage in the run's word, and no FAILED in capitals.
      expect(end.outcome.text).toContain('GRASP_CLOSE');
      expect(end.text).not.toContain('FAILED');
      // C4: the stop's own reading, never the far-edge pixels of another action, and no frame (the failure names none).
      expect(end.outcome.text).toContain('base_zero');
      expect(end.outcome.text).toContain('1.17 deg/s');
      expect(end.outcome.text).not.toContain('straddle_far_edge_row');
      expect(end.outcome.text).not.toContain('px');
      expect(end.markup).not.toContain('data-failure-frame');
      // C6: holding read from the record, with the gripper reading.
      expect(end.outcome.holding).toBe('true');
      expect(end.outcome.text).toContain('Holding the block');
      expect(end.outcome.text).toContain('gripper 68.12');
      // C5 and top 7: no Try again, no primary control, Start on beside the held block.
      expect(end.offered).not.toContain('retry');
      expect(end.primary).toEqual([]);
      expect(end.startOff).toBe(false);
      // C10 and T1: two cards, the refusal muted (no expectation served) and never replaced.
      expect(end.activations.map((card) => card.verdict)).toEqual(['refused_by_cake', 'activated']);
      // C11: the signature check is an earlier session's, and says so.
      expect(end.signature).toEqual({ stale: 'true', verdict: 'sig_earlier' });
      // D2: the live slot footer never credits the refused abort-swap: not at the hold, not once the activations were
      // pressed (while the robot may still read generation 1), not on any render.
      expect(played.afterActivations).not.toBeNull();
      for (const screen of [played.hold, played.afterActivations, ...played.all]) {
        expect(screen?.cake ?? '').not.toContain('Set by the activation of mobile-demo-active-successor-abort-swap');
      }
      // N1 (browser r2): right after the accepted swap, under the card "Activated, slot 1 -> 2", neither the live slot
      // footer nor the Scene says the capability is not active; each says the newest record's slot with its time.
      const swapped = played.afterActivations;
      expect(swapped?.activations.map((card) => card.verdict)).toEqual(['refused_by_cake', 'activated']);
      expect(swapped?.cake ?? '').toContain('Live slot: mobile_approach, active, generation 2');
      expect(swapped?.cake ?? '').not.toContain('not active');
      expect(swapped?.text ?? '').not.toContain('which is not active');
      expect(swapped?.cake ?? '').toMatch(/Live slot: [^.]*\d{2}:\d{2}:\d{2}Z/);
      // T3: run 2's group lists its eight states, each with a time, and the joined cause.
      const group = [...end.markup.matchAll(/data-group="synthetic-run-0002"[^>]*>([^<]*)/g)].map((match) => match[1] ?? '');
      expect(group.filter((line) => /^[A-Z_]+( again)? \d{2}:\d{2}:\d{2}Z$/.test(line)).map((line) => line.replace(/ \d.*$/, ''))).toEqual([
        'SEARCH', 'TARGET_SELECTED', 'PREGRASP', 'ALIGN', 'ALIGN again', 'APPROACH', 'STRADDLING', 'GRASP_CLOSE',
      ]);
      expect(group.some((line) => line.startsWith('base_zero: 0 reads against 3 reads'))).toBe(true);
      // T5: Ended is run 2's own finish.
      const finish = /data-group="synthetic-run-0002" data-t="([^"]+)" data-verdict="stopped_by_controller"/.exec(end.markup)?.[1] ?? null;
      expect(finish).not.toBeNull();
      expect(end.outcome.ended).toBe(finish);
    },
  },
};

/* ---- the checks -------------------------------------------------------------------------------------------------- */

function expectMoment(where: string, screen: ScreenRead | null, moment: Moment): void {
  expect(screen, where).not.toBeNull();
  if (screen === null) return;
  expect(screen.mission, `${where}: the mission`).toBe(moment.mission);
  if (moment.waiting !== undefined) expect(screen.waiting, `${where}: the waiting reason`).toBe(moment.waiting);
  // THE OUTCOME CARD.
  const card = moment.card;
  expect(screen.outcome.card, `${where}: the card`).toBe(card.state);
  if (card.mission !== undefined) expect(screen.outcome.mission, `${where}: the mission's word`).toBe(card.mission);
  if (card.run !== undefined) expect(screen.outcome.run, `${where}: the run's word`).toBe(card.run);
  if (card.cause !== undefined) expect(screen.outcome.cause, `${where}: the cause`).toBe(card.cause);
  if (card.frame !== undefined) expect(screen.outcome.frame ?? '', `${where}: the failure's own frame`).toContain(card.frame);
  if (card.limit !== undefined) expect(screen.outcome.limit, `${where}: the limit`).toBe(`Decided by ${card.limit}`);
  if (card.steps !== undefined) expect(screen.outcome.steps, `${where}: the steps`).toEqual(card.steps);
  // THE OFFERED ACTIONS, THE STAGES, THE READINESS VERDICT.
  expect(screen.offered, `${where}: the offered actions`).toEqual(moment.offered);
  expect(screen.stages, `${where}: the stages`).toEqual(moment.stages);
  expect(screen.readiness, `${where}: the readiness verdict`).toBe(moment.readiness);
  if (moment.readySays !== undefined) expect(screen.readinessSentence.toLowerCase(), `${where}: the readiness sentence`).toContain(moment.readySays.toLowerCase());
  // THE CAKE REGION.
  for (const words of moment.cake) expect(screen.cake, `${where}: the Cake region`).toContain(words);
  for (const words of moment.cakeNot) expect(screen.cake, `${where}: the Cake region`).not.toContain(words);
}

const STEPS: Step[] = ['find', 'pick', 'carry', 'place', 'return'];
const ENDED_BADLY: readonly Status[] = ['failed', 'stopped_by_controller', 'stopped_on_request', 'refused', 'no_outcome'];

/** What must hold on every render while a scenario plays. `robotHeldUntil`: the renders taken while motion is held. */
function expectEveryRender(name: string, played: Played, robotHeldUntil: number): void {
  played.all.forEach((screen, index) => {
    const where = `${name}, render ${String(index)}`;
    // The card's run line names the run on the screen: never a finished earlier run while a later one is in progress.
    if (screen.outcome.runId !== null && screen.active !== null) expect(screen.outcome.runId, `${where}: the card's run`).toBe(screen.active);
    // No stage ends badly without a cause stated on the card.
    for (const step of STEPS) {
      if (!ENDED_BADLY.includes(screen.stages[step])) continue;
      expect(screen.outcome.cause, `${where}: ${step} ended without a cause`).not.toBeNull();
      // The card's run ended in the very stage the stages draw as ended badly (verify-r1 C7).
      expect(screen.outcome.runStage, `${where}: the card's run stage`).toBe(step);
    }
    // The base's return pulses belong to Return: once Return is under way, Carry is never drawn in progress again.
    if (screen.stages.return === 'active') expect(screen.stages.carry, `${where}: carry during the return`).not.toBe('active');
    // While a mission is carried, only what the runtime's lists offer is on.
    if (screen.mission === 'RUNNING') expect(screen.offered.filter((id) => id !== 'stop'), `${where}: running`).toEqual([]);
    if (screen.mission === 'WAITING_FOR_CAPABILITY' && screen.waiting === 'capability_unavailable') {
      expect(screen.offered.filter((id) => id !== 'stop' && id !== 'candidate'), `${where}: held for the capability`).toEqual([]);
    }
    if (screen.mission === 'WAITING_FOR_CAPABILITY' && screen.waiting === 'operator_decision') {
      const offers = screen.holdOffers ?? [];
      const retry = offers.includes('retry') || offers.includes('move_block_then_retry');
      expect(screen.offered, `${where}: the hold offers ${offers.join(', ')}`).toEqual(retry ? ['stop', 'retry'] : ['stop']);
    }
    // The verdict never says Ready beside an off Start, nor while the robot's motion is held.
    if (screen.startOff) expect(screen.readiness, `${where}: ready beside an off Start`).not.toBe('ready');
    if (index < robotHeldUntil) expect(screen.readiness, `${where}: ready while motion is held`).not.toBe('ready');
    // The camera chip never says Live over a panel that says there is no camera.
    if (screen.chip.includes('No camera in a rehearsal')) expect(screen.chip.startsWith('Live'), `${where}: the chip`).toBe(false);
    expect(screen.outcome.text, where).not.toMatch(/\bthe the\b/);
  });
}

const FORBIDDEN = ['Live demonstration', 'LIVE RUNTIME', 'PHYSICAL', 'SYNTHETIC', 'by design'];

/**
 * THE CROSS-SCENARIO INVARIANTS (spec 8.1). Over the playing tab's renders in order: activation cards are append-only
 * (INV-1), timeline keys are never removed (INV-2) and an ended mission card, once drawn, stays (INV-10). Over every
 * render, the playing tab's and the extra tabs': at most one primary control (INV-3); with no mission carried and no run
 * active, Not ready exactly when Start is off and Start primary exactly when Ready (INV-4); exactly one Stop (INV-5); none
 * of the retired words (INV-6); every run id verbatim, never upper-cased, and no text-transform (INV-7); no "ago" and
 * every data-t in UTC (INV-8). And the resting screen is byte-identical 20 s later (INV-9). Each press is one line.
 */
function expectInvariants(name: string, played: Played): void {
  played.all.forEach((screen, index) => {
    const next = played.all[index + 1];
    if (next === undefined) return;
    const where = `${name}, render ${String(index)} to ${String(index + 1)}`;
    const ids = screen.activations.map((card) => card.id);
    expect(next.activations.map((card) => card.id).slice(0, ids.length), `${where}: INV-1 activation cards append-only`).toEqual(ids);
    for (const key of screen.timeline) expect(next.timeline, `${where}: INV-2 timeline key ${key} kept`).toContain(key);
    if (screen.outcome.card === 'expanded') expect(next.outcome.card, `${where}: INV-10 the ended card stays`).toBe('expanded');
  });
  [...played.all, ...played.extra, played.end, played.again].forEach((screen, index) => {
    const where = `${name}, screen ${String(index)}`;
    expect(screen.primary.length, `${where}: INV-3 at most one primary (${screen.primary.join(', ')})`).toBeLessThanOrEqual(1);
    const carried = screen.mission === 'RUNNING' || screen.mission === 'WAITING_FOR_CAPABILITY';
    // A run is active when the runtime reports one or this tab's fold of its records says one is (readiness Running).
    if (!carried && screen.active === null && screen.readiness !== 'running') {
      expect(screen.readiness === 'not_ready', `${where}: INV-4 Not ready iff Start is off`).toBe(screen.startOff);
      expect(screen.primary.includes('start'), `${where}: INV-4 Start primary iff Ready`).toBe(screen.readiness === 'ready');
    }
    expect(screen.stopControls, `${where}: INV-5 one Stop`).toBe(1);
    for (const word of FORBIDDEN) expect(screen.text, `${where}: INV-6 ${word}`).not.toContain(word);
    for (const runId of screen.runIds) {
      expect(screen.markup, `${where}: INV-7 ${runId} verbatim`).toContain(runId);
      expect(screen.markup, `${where}: INV-7 ${runId} upper-cased`).not.toContain(runId.toUpperCase());
    }
    expect(screen.markup, `${where}: INV-7 text-transform`).not.toContain('text-transform');
    expect(screen.text, `${where}: INV-8 no age`).not.toMatch(/\b\d+ ?(s|min|h) ago\b/);
    // C13: each activation the mission records is drawn as exactly one card on the main screen, and no card is drawn
    // for an operation the mission does not record.
    for (const operation of screen.operations) {
      expect(screen.activations.filter((card) => card.id === operation).length, `${where}: C13 one card for ${operation}`).toBe(1);
    }
    for (const card of screen.activations) expect(screen.operations, `${where}: C13 card ${card.id} is a mission record`).toContain(card.id);
    for (const t of screen.times) expect(t, `${where}: INV-8 data-t in UTC`).toMatch(/Z$/);
  });
  expect(played.again.markup, `${name}: INV-9 the resting screen 20 s later`).toBe(played.end.markup);
  // T2: each press on this display is one line of the timeline, none overwriting another.
  expect(played.end.timelinePresses, `${name}: one timeline line per press`).toBe(played.receipts.length);
}

/**
 * What must hold on a tab opened afresh while a run is in progress (a reload, a second display), and on the playing tab
 * in the moments just after a press: nothing of a finished earlier run is drawn as the run in progress.
 */
function expectNoEarlierEnd(name: string, screens: readonly ScreenRead[]): void {
  screens.forEach((screen, index) => {
    const where = `${name}, extra render ${String(index)} (active ${String(screen.active)})`;
    if (screen.active === null) return;
    if (screen.outcome.runId !== null) expect(screen.outcome.runId, `${where}: the card's run`).toBe(screen.active);
    for (const step of STEPS) {
      if (!ENDED_BADLY.includes(screen.stages[step])) continue;
      expect(screen.outcome.cause, `${where}: ${step} ended without a cause`).not.toBeNull();
      // The card's run ended in the very stage the stages draw as ended badly (verify-r1 C7).
      expect(screen.outcome.runStage, `${where}: the card's run stage`).toBe(step);
    }
  });
}

describe('the demo screen, for every scenario of the catalogue, at its start, its hold and its end', () => {
  beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T05:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('has the recorded facts of every scenario the catalogue lists', () => {
    expect(Object.keys(FACTS).sort()).toEqual(SCENARIO_CATALOGUE.map((spec) => spec.name).sort());
  });

  for (const spec of SCENARIO_CATALOGUE) {
    it(`${spec.name}: the outcome card, the offered actions, the stages, the readiness verdict and the Cake region say what the scenario recorded`, async () => {
      const facts = FACTS[spec.name];
      const played = await playScenario(spec);
      expectMoment(`${spec.name} at the start`, played.start, facts.start);
      if (facts.hold === null) expect(played.hold, `${spec.name} never holds`).toBeNull();
      else expectMoment(`${spec.name} at the hold`, played.hold, facts.hold);
      expectMoment(`${spec.name} at the end`, played.end, facts.end);
      facts.more?.(played);
      // A bring-up robot holds motion until the confirmation, which the harness presses after its first two renders; a
      // host outside Cake holds it throughout.
      const heldUntil = spec.robot === 'host_outside_cake' ? played.all.length : spec.robot === 'not_confirmed' ? 2 : 0;
      expectEveryRender(spec.name, played, heldUntil);
      expectInvariants(spec.name, played);
    }, 60_000);
  }

  for (const spec of SCENARIO_CATALOGUE) {
    it(`${spec.name}: a tab opened while a run is in progress, and every render just after a press, draw no earlier run's end`, async () => {
      const played = await playScenario(spec, { reloads: true });
      const ran = played.all.some((screen) => screen.active !== null);
      if (ran) expect(played.extra.some((screen) => screen.active !== null), `${spec.name}: a tab was opened during a run`).toBe(true);
      expectNoEarlierEnd(spec.name, played.extra);
      expectInvariants(`${spec.name} (reloads)`, played);
    }, 120_000);
  }

  for (const spec of SCENARIO_CATALOGUE) {
    it(`${spec.name}: every control drawn on at the start and at the end is taken when it is pressed`, async () => {
      const played = await playScenario(spec, { presses: true });
      expect(played.presses.filter((press) => press.refused !== null), `${spec.name}: refused presses`).toEqual([]);
      // A scenario that begins with Start offers it at its start, and the press begins the mission.
      if (spec.start_refusal === undefined && (spec.robot === undefined || spec.robot === 'bench')) {
        expect(played.presses.some((press) => press.moment === 'start' && press.control === 'start'), `${spec.name}: Start pressed`).toBe(true);
      }
    }, 120_000);
  }

  it('scene-scan, Scan scene pressed on its own: a completed scan states no failure and fails no stage', async () => {
    const spec = SCENARIO_CATALOGUE.find((item) => item.name === 'scene-scan');
    if (spec === undefined) throw new Error('no scene-scan scenario');
    const played = await playScenario(spec, { variant: 'scan-first' });
    for (const screen of played.all) {
      expect(screen.outcome.cause).toBeNull();
      for (const ended of ENDED_BADLY) expect(Object.values(screen.stages)).not.toContain(ended);
    }
    expectMoment('the scan alone, at the end', played.end, { ...START, offered: ['start', 'scan_scene'], stages: { ...NONE, find: 'entered' } });
  }, 60_000);

  it('hold-then-retry-placed, Stop at the hold: STOPPED, the failed attempt still shown, and what the runtime lists after it', async () => {
    const spec = SCENARIO_CATALOGUE.find((item) => item.name === 'hold-then-retry-placed');
    if (spec === undefined) throw new Error('no hold-then-retry-placed scenario');
    const played = await playScenario(spec, { stopAtHold: true });
    // The runtime ends STOPPED and keeps the waiting reason; after a stop at a hold it lists Return arm (the arm moved) and
    // a new mission. Stop is off: nothing is running.
    expectMoment('stopped at the hold', played.end, stoppedAtHold());
    expectStopped(played.end);
    expectEveryRender(spec.name, played, 0);
  }, 60_000);

  it('hold-live-default, Stop at the hold (the only action it offers): STOPPED, said as stopped, and what the runtime lists after it', async () => {
    const spec = SCENARIO_CATALOGUE.find((item) => item.name === 'hold-live-default');
    if (spec === undefined) throw new Error('no hold-live-default scenario');
    const played = await playScenario(spec, { stopAtHold: true });
    expect(played.hold?.offered).toEqual(['stop']);
    expectMoment('stopped at the default LIVE hold', played.end, stoppedAtHold());
    expectStopped(played.end);
    expectEveryRender(spec.name, played, 0);
  }, 60_000);
});

/** The screen after a Stop at a decision hold: the mission stopped on request, its failed attempt still stated. */
function stoppedAtHold(): Moment {
  return {
    mission: 'STOPPED',
    waiting: 'operator_decision',
    card: { state: 'expanded', mission: 'stopped_on_request', run: 'failed', cause: 'align_cube_not_detected', frame: '-01-descent', steps: ['return_arm', 'start'] },
    offered: ['start', 'return_arm'],
    stages: PICK_FAILED,
    readiness: 'steps_first',
    readySays: 'Return arm',
    cake: NO_ROBOT,
    cakeNot: ['The mission waits for Cake'],
  };
}

/** After a Stop at a hold: the screen says the mission was stopped, and never that it is held or still stopping. */
function expectStopped(screen: ScreenRead): void {
  expect(screen.outcome.text).toContain('Mission stopped on request');
  expect(screen.text).not.toMatch(/held for (your|a) decision/);
  expect(screen.text).not.toMatch(/\bis stopping\b/);
}
