// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE STAGE TIMELINE: Find, Pick, Carry, Place, Return, for ONE target, across every run the mission recorded for it.
//
// The rail in `design/steps5.ts` says where a run is. This says what each stage DECIDED and on what: its verdict, two
// or three of the numbers it decided with, the frame it decided on, and the rule that decided. Everything on a row is
// read from a record the controller wrote; nothing is computed here that the controller did not state.
//
// Four rules hold it together:
//
//  1. THE NUMBERS ARE THE RECORD'S. A failed stage shows the values the raise site carried first, then the measured
//     value of the stage's predicates against their thresholds, each under the name the controller gave it. A number is
//     printed as recorded, rounded to two decimals, never converted.
//  2. THE RULE IS NAMED ONLY WHEN THE RECORD NAMES IT. `rule_ref` and `rule_digest` travel on `predicate.evaluated`; a
//     record without them says "rule not recorded", never a guess at which rule it was.
//  3. RETRIES STAY INSIDE THEIR STAGE. An ALIGN attempt or a second close is counted on the Pick row; an earlier RUN of
//     the same target (a mission retry) is listed on the row where that run ended, never merged into the newest run's
//     verdict.
//  4. NO STAGE ROW CARRIES CAKE TEXT (docs/autonomous-demo/console-redesign-2026-09-27.md, section 7.2; audit C8). The
//     activations are the Cake region's, one card each; a stage row says only what the run's records say about it.
//  5. THE STAGE WHERE A RUN ENDED CARRIES THE RUN'S WORD WITH "HERE" (section 5.1; audit C3): "Stopped here by the
//     controller", "Failed here", "Refused here". A stop is never drawn as a failure.

import type { NamedValue } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import type { MissionRecord, MissionTargetRecord } from './missionRecord';
import { parseUtcMs } from './utcTime';
import { CAPABILITY_HOLD_CODES, RUN_WORD_HERE, runVerdict } from './verdict';
import { PHASE_STATUS_WORDS, PRESENTATION_STEPS, PRESENTATION_STEP_LABEL, PRESENTATION_STEP_PREDICATE, presentationStepInContext, pulseStepBefore } from './phase';
import type { PhaseStatus, PresentationStep } from './phase';
import { presentationSteps } from '../design/steps5';
import { predicateFailed } from './predicate';
import { commandScope, nonPlacementCommandSucceeded } from './command';
import { outcomeIsOfRunOnScreen } from './steps';
import type { PredicateView, ViewState } from './types';

/**
 * Which stage each predicate the controller emits belongs to. Read from `run_single_cube_cycle.py`'s own
 * `self.events.predicate(...)` call sites. A predicate outside this table is placed by its `rule_ref` prefix when it
 * carries one, and otherwise on no row: it is still in Diagnostics, where every predicate is.
 */
const PREDICATE_STEP: Record<string, PresentationStep> = {
  start_pose_is_the_low_scan_pose: 'find',
  cube_seen_with_a_contact_point: 'find',
  sweep_sector_cube_seen_with_a_contact_point: 'find',
  already_placed: 'find',

  pregrasp_pan_within_return_bound: 'pick',
  cube_detected_in_the_pregrasp_view: 'pick',
  aligned: 'pick',
  trim_pan_within_return_bound: 'pick',
  straddling: 'pick',
  closed_on_something: 'pick',
  gripper_in_its_declared_phase: 'pick',
  object_secured: 'pick',

  placement_plan: 'carry',
  arm_only_sufficient: 'carry',
  pad_frame_valid_while_holding: 'carry',
  base_measured: 'carry',
  arm_held_during_base_translation: 'carry',
  base_posture_at_the_low_scan_pose: 'carry',
  capability_output_applied: 'carry',
  reach_carry: 'carry',

  release_admitted: 'place',
  release_height: 'place',
  release_margin: 'place',
  release_hold: 'place',
  placed: 'place',
  clearance_target_met: 'place',

  base_returned: 'return',
};

/** The stage names of a `rule_ref` ("<stage>.<selection>"), for a predicate the table above does not know. */
const RULE_STAGE: Record<string, PresentationStep> = {
  search: 'find',
  align: 'pick',
  grasp: 'pick',
  placement: 'carry',
  return: 'return',
};

/**
 * The predicates that decide each stage, first first. The first of each is the stage's own verdict
 * (`PRESENTATION_STEP_PREDICATE`); Carry has none, so its plan leads. The numbers of a row are read in this order.
 */
const STEP_DECIDERS: Record<PresentationStep, readonly string[]> = {
  find: [PRESENTATION_STEP_PREDICATE.find ?? 'cube_seen_with_a_contact_point', 'already_placed', 'start_pose_is_the_low_scan_pose'],
  pick: [PRESENTATION_STEP_PREDICATE.pick ?? 'object_secured', 'closed_on_something', 'straddling', 'aligned', 'cube_detected_in_the_pregrasp_view'],
  carry: ['placement_plan', 'arm_only_sufficient', 'base_measured', 'capability_output_applied'],
  place: [PRESENTATION_STEP_PREDICATE.place ?? 'placed', 'release_margin', 'release_admitted', 'release_height'],
  return: [PRESENTATION_STEP_PREDICATE.return ?? 'base_returned'],
};

/** The states the controller records once per attempt, and the stage each attempt is counted in. */
const ATTEMPT_STATES: Record<string, PresentationStep> = {
  SEARCH_SWEEP: 'find',
  ALIGN: 'pick',
  GRASP_CLOSE: 'pick',
  GRASP_RETRY_ADVANCE: 'pick',
  BASE_ADVANCE: 'carry',
  BASE_RETURN: 'return',
};

const BAD_OUTCOMES: ReadonlySet<string> = new Set(['failed', 'stopped', 'refused', 'unknown']);

/**
 * Reason codes that end a run BY DESIGN of the mission, not by a stage failing: the run measured that the target needs
 * the base capability and the mission holds for it. The stage is entered, its code is kept on the row, and the hold is
 * drawn on Carry; the stage is never drawn as failed.
 */
export const MISSION_HOLD_CODES: ReadonlySet<string> = CAPABILITY_HOLD_CODES;

export function stepOfPredicate(
  predicate: Pick<PredicateView, 'predicate' | 'rule_ref'> & { seq?: number },
  history: readonly { state: string; seq: number }[] = [],
): PresentationStep | null {
  // base_measured is recorded after every pulse: after a return pulse it is the Return's measurement.
  if (predicate.predicate === 'base_measured' && predicate.seq !== undefined) {
    return pulseStepBefore(history.filter((item) => item.seq < (predicate.seq as number)).map((item) => item.state));
  }
  const named = PREDICATE_STEP[predicate.predicate];
  if (named !== undefined) return named;
  const stage = predicate.rule_ref === null ? null : predicate.rule_ref.split('.')[0] ?? null;
  return stage === null ? null : RULE_STAGE[stage] ?? null;
}

/** A recorded number as text: as recorded, rounded to two decimals, with its unit when the record carried one. */
export function recordedNumber(value: number | string | boolean | null, unit: string | null): string | null {
  if (value === null) return null;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return unit === null ? value : `${value} ${unit}`;
  if (!Number.isFinite(value)) return null;
  const rounded = Math.round(value * 100) / 100;
  return unit === null || unit === '' ? String(rounded) : `${String(rounded)} ${unit}`;
}

export interface StageNumber {
  /** The name the controller gave the value. */
  name: string;
  text: string;
}

export interface StageRule {
  ref: string;
  digest: string | null;
}

export interface EarlierAttempt {
  run_id: string;
  /** The outcome that run reported, as reported. */
  outcome: string | null;
  reason_code: string | null;
}

export interface StageRow {
  step: PresentationStep;
  label: string;
  status: PhaseStatus;
  words: string;
  /** At most three, in the order: the failure's own values, then the predicates' measured values. */
  numbers: StageNumber[];
  /** The observation the stage decided on, for the thumbnail; null when no record of this stage names one. */
  frame_id: string | null;
  /**
   * For the stage the run FAILED in: the frame the failure itself named, else the frame of the stage's own failing
   * predicate, else null. Never a frame of a passing predicate, and never another stage's.
   */
  failed_frame_id: string | null;
  /** The rule that decided, or null when the record names none (every record before the stage seams). */
  rule: StageRule | null;
  /** The predicate the rule and the numbers were read from, so a reader can find it in Diagnostics. */
  decided_by: string | null;
  /** Per-attempt states this run entered more than once inside this stage, with the count. */
  retries: { state: string; count: number }[];
  /** Earlier runs of the same target that ended in this stage. */
  earlier: EarlierAttempt[];
  /** When the newest run first entered this stage (its first state transition into it), as ISO UTC; null when not. */
  entered_at: string | null;
  /** The reason code when the newest run ended in this stage. */
  failure_code: string | null;
}

export interface StageTimeline {
  rows: StageRow[];
  /** The runs the rows were built from, oldest first. The newest decides every verdict. */
  run_ids: string[];
  basis: string;
}

/** One folded run, oldest first, as the store holds it. */
export interface FoldedRun {
  run_id: string;
  view: ViewState;
  /** The run's kept events (the store's `missionRunEvents`), for the times; the fold's recent events otherwise. */
  events?: readonly ContractEvent[];
}

function newestFirst(predicates: readonly PredicateView[]): PredicateView[] {
  return [...predicates].sort((a, b) => b.seq - a.seq);
}

function firstFrame(predicate: PredicateView): string | null {
  for (const piece of predicate.evidence) {
    if (piece.type !== 'frame' && piece.type !== 'frame_pair') continue;
    const id = piece.observation_ids[0];
    if (id !== undefined && id !== '') return id;
  }
  return null;
}

function pushNumber(out: StageNumber[], name: string, text: string | null): void {
  if (text === null || out.length >= 3 || out.some((item) => item.name === name)) return;
  out.push({ name, text });
}

function numberOfValue(item: NamedValue): string | null {
  return recordedNumber(item.value as number | string | boolean | null, item.unit ?? null);
}

/**
 * A placement refusal that names its binding limit leads with it: the constraint, its deficit and the edge it binds on,
 * then the rest in the record's order. Its sentence and hint are texts, stated whole in the banner's limit line
 * (`placementLimit`), and are not cut into a number row. A record naming no constraint keeps its own order.
 */
const LIMIT_FIRST = ['constraint', 'deficit_mm', 'binding_edge'];
const LIMIT_TEXTS: ReadonlySet<string> = new Set(['explanation', 'hint', 'hint_places_block', 'model_admits_under']);

function failureNumbersInOrder(measurements: readonly NamedValue[]): NamedValue[] {
  if (!measurements.some((item) => item.name === 'constraint' && typeof item.value === 'string')) return [...measurements];
  const first = LIMIT_FIRST.flatMap((name) => measurements.filter((item) => item.name === name));
  return [...first, ...measurements.filter((item) => !LIMIT_FIRST.includes(item.name) && !LIMIT_TEXTS.has(item.name))];
}

function predicateNumbers(out: StageNumber[], predicate: PredicateView): void {
  const measured = recordedNumber(predicate.measured, predicate.unit);
  if (measured !== null) {
    const threshold = recordedNumber(predicate.threshold, predicate.unit);
    pushNumber(out, predicate.predicate, threshold === null ? measured : `${measured} against ${threshold}`);
  }
  for (const item of predicate.measurements) pushNumber(out, item.name, numberOfValue(item));
}

/** Where the newest run ended, when it ended badly: the stage its last state belongs to. */
function failedStep(view: ViewState): PresentationStep | null {
  const outcome = view.outcome;
  if (outcome.reported === null || !BAD_OUTCOMES.has(outcome.reported)) return null;
  // An earlier run's end fails no stage of the run in progress: its phase, or the live state it would fall back to, is
  // where THIS run is now, not where that one ended.
  if (!outcomeIsOfRunOnScreen(view)) return null;
  // A command with no placement in it that ended well (a completed scene scan, a Look) failed no stage: its `unknown`
  // outcome says only that there was no placement to name an outcome after (`model/command.ts`).
  const scope = commandScope(view);
  if (!scope.requires_placement && nonPlacementCommandSucceeded(view)) return null;
  const phase = outcome.phase ?? view.machine?.state ?? null;
  if (phase === null) return null;
  const step = presentationStepInContext(phase, (view.machine?.history ?? []).map((item) => item.state));
  return step === 'terminal' || step === 'unknown' ? null : step;
}

function retriesIn(view: ViewState, step: PresentationStep): { state: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const item of view.machine?.history ?? []) {
    if (ATTEMPT_STATES[item.state] !== step) continue;
    counts.set(item.state, (counts.get(item.state) ?? 0) + 1);
  }
  const out: { state: string; count: number }[] = [];
  for (const [state, count] of counts) if (count > 1) out.push({ state, count });
  return out;
}

function emptyRow(step: PresentationStep, status: PhaseStatus): StageRow {
  return {
    step,
    label: PRESENTATION_STEP_LABEL[step],
    status,
    words: PHASE_STATUS_WORDS[status],
    numbers: [],
    frame_id: null,
    failed_frame_id: null,
    rule: null,
    decided_by: null,
    retries: [],
    earlier: [],
    entered_at: null,
    failure_code: null,
  };
}

/**
 * The five rows for the runs given, oldest first; the NEWEST run decides each verdict. With no run the five rows are
 * "not reached", and a mission target that is waiting still gets its Cake marker.
 */
// `_mission` is kept in the signature for the callers that pass it; no stage row reads the mission's Cake records any more.
export function stageTimeline(runs: readonly FoldedRun[], _mission: MissionRecord | null = null, target: MissionTargetRecord | null = null): StageTimeline {
  const newest = runs[runs.length - 1] ?? null;
  if (newest === null) {
    const rows = PRESENTATION_STEPS.map((step) => emptyRow(step, 'not_reached'));
    return { rows, run_ids: [], basis: target === null ? 'no run has been folded here' : `no run of ${target.label} has been folded here` };
  }

  const view = newest.view;
  const progress = presentationSteps(view, newest.run_id).rows;
  const endedIn = failedStep(view);
  const byStep = new Map<PresentationStep, PredicateView[]>();
  for (const predicate of newestFirst(view.predicates)) {
    const step = stepOfPredicate(predicate, view.machine?.history ?? []);
    if (step === null) continue;
    byStep.set(step, [...(byStep.get(step) ?? []), predicate]);
  }

  const entered = enteredTimes(newest);
  // A RUN THAT ENDED WITH ITS OWN RECORDS NOT READ (verifier r2 defect 6): the fold holds only the snapshot's outcome, so
  // no row can say how far the run got or which word ended it. Every row says so, as the card gives no run word (V13).
  const unread = endedIn !== null && !runRecordsRead(newest);
  const rows = PRESENTATION_STEPS.map((step, index) => {
    const base = progress[index];
    let status: PhaseStatus = unread ? 'not_observed' : (base?.status ?? 'not_reached');
    const held = MISSION_HOLD_CODES.has(view.outcome.reason_code ?? '');
    if (endedIn === step && !held && !unread) status = endedStatus(view, newest.events);
    const row = emptyRow(step, status);
    if (status !== 'failed' && RUN_WORD_HERE[status] !== undefined) row.words = RUN_WORD_HERE[status] ?? row.words;
    if (status === 'failed' && endedIn === step) row.words = RUN_WORD_HERE['failed'] ?? row.words;
    row.entered_at = entered.get(step) ?? null;
    const predicates = byStep.get(step) ?? [];
    // The stage's deciding predicates first, in the order the stage decides on them; then the newest of the others.
    const deciding = STEP_DECIDERS[step];
    const rank = (item: PredicateView): number => {
      const at = deciding.indexOf(item.predicate);
      return at < 0 ? deciding.length : at;
    };
    const ordered = [...predicates].sort((a, b) => rank(a) - rank(b));
    // A stage the run ended in badly decides on its false predicate, when one exists.
    const ended = endedIn === step && !held;
    const failing = ended ? ordered.find(predicateFailed) : undefined;
    const decider = failing ?? ordered[0] ?? null;

    if (endedIn === step) {
      row.failure_code = view.outcome.reason_code;
      for (const item of failureNumbersInOrder(view.outcome.failure?.measurements ?? [])) pushNumber(row.numbers, item.name, numberOfValue(item));
      row.frame_id = view.outcome.failure?.observation_ids[0] ?? null;
    }
    if (decider !== null) {
      predicateNumbers(row.numbers, decider);
      row.decided_by = decider.predicate;
      if (decider.rule_ref !== null) row.rule = { ref: decider.rule_ref, digest: decider.rule_digest };
      row.frame_id = row.frame_id ?? firstFrame(decider);
    }
    for (const other of ordered) {
      if (other === decider) continue;
      predicateNumbers(row.numbers, other);
      row.frame_id = row.frame_id ?? firstFrame(other);
    }
    if (ended) row.failed_frame_id = view.outcome.failure?.observation_ids[0] ?? (failing === undefined ? null : firstFrame(failing));
    row.retries = retriesIn(view, step);
    return row;
  });

  // EARLIER RUNS OF THE SAME TARGET, each on the row where it ended. Its verdicts never become the newest run's.
  for (const earlier of runs.slice(0, -1)) {
    const where = failedStep(earlier.view);
    if (where === null) continue;
    const row = rows.find((item) => item.step === where);
    row?.earlier.push({ run_id: earlier.run_id, outcome: earlier.view.outcome.reported, reason_code: earlier.view.outcome.reason_code });
  }

  const basis =
    runs.length === 1
      ? `the records of run ${newest.run_id}`
      : `the records of run ${newest.run_id}, the newest of ${String(runs.length)} runs${target === null ? '' : ` of ${target.label}`}`;
  return { rows, run_ids: runs.map((item) => item.run_id), basis };
}

/**
 * Whether the run's own records are read: its kept events (the store's `missionRunEvents`), or a fold that saw the run
 * begin (the live fold that watched it, or its read-back fold).
 */
function runRecordsRead(run: FoldedRun): boolean {
  return run.events !== undefined || (run.view.run_started?.run_id ?? null) === run.run_id;
}

/** The status of the stage a run ended in, in the run's own word: a stop the controller made is not a failure. */
function endedStatus(view: ViewState, events: readonly ContractEvent[] | undefined): PhaseStatus {
  const verdict = runVerdict({
    outcome: view.outcome.reported,
    reason_code: view.outcome.reason_code,
    // The run's own events say whether a stop was asked for, where they are read; else the fold that saw the run.
    stop_requested: events !== undefined ? events.some((event) => event.kind === 'stop.requested') : view.stop.requested !== null,
    placement: true,
    non_placement_done: false,
  });
  switch (verdict.id) {
    case 'stopped_by_controller':
    case 'stopped_on_request':
    case 'refused':
    case 'no_outcome':
      return verdict.id;
    default:
      return 'failed';
  }
}

/** When the run first entered each stage: the recorded time of its first state transition into it. */
function enteredTimes(run: FoldedRun): Map<PresentationStep, string> {
  const out = new Map<PresentationStep, string>();
  const events = run.events ?? run.view.recent_events;
  const states: string[] = [];
  for (const event of events) {
    if (event.kind !== 'state.transition') continue;
    const to = event.payload.to_state;
    const step = presentationStepInContext(to, states);
    states.push(to);
    if (step === 'terminal' || step === 'unknown' || out.has(step)) continue;
    const ms = parseUtcMs(event.t_wall_utc);
    if (ms !== null) out.set(step, new Date(ms).toISOString());
  }
  return out;
}

/** The numbers of a row as one line, for a title attribute or a narrow screen. */
export function stageNumbersLine(row: StageRow): string {
  return row.numbers.map((item) => `${item.name} ${item.text}`).join('; ');
}

/** The rule of a row in words: the reference and the digest's first twelve characters, or that none was recorded. */
export function stageRuleWords(row: StageRow): string {
  if (row.rule === null) return 'rule not recorded';
  const digest = row.rule.digest === null ? null : row.rule.digest.replace(/^sha256:/, '').slice(0, 12);
  return digest === null ? row.rule.ref : `${row.rule.ref} @ ${digest}`;
}
