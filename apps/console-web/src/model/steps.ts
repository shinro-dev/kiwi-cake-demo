// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FIVE STEPS, AND WHICH RUN A RESULT BELONGS TO.
//
// The screen is a sequence: connect, observe, run, result. That sequence is a PRESENTATION of runtime state and never
// a thing an operator advances. Nothing in this file commands anything, nothing here completes a step, and clicking a
// step changes only what is being inspected. The step the console is ON is derived here, from the record, every time
// it renders.
//
// THE PART OF THIS FILE THAT IS CORRECTNESS RATHER THAN LAYOUT is the run scoping. A snapshot's `last_outcome` is the
// PREVIOUS run whenever a different one is active, and the runtime says so in the contract. A screen that reads it as
// the current run's result shows the last success as the result of the run that just failed. So every result the
// screen draws is asked for BY RUN ID here, and a result whose run id is not the run being looked at is either shown
// as the previous run, with the time it finished, or not shown at all. There is no third path.

import { UNSCOPED_COMMAND } from './command';
import type { CommandScope } from './command';
import { freshness } from './format';
import { phaseOfState, isTerminalState } from './phase';
import { robotReading } from './robot';
import type { OutcomeView, ViewState } from './types';

/** The five steps, in order. Step 5 is not part of the baseline demonstration and is never reached by a run. */
export type StepId = 1 | 2 | 3 | 4 | 5;

export interface StepDefinition {
  id: StepId;
  /** The label on the step. Two words at most: the guide is read at a glance, not studied. */
  name: string;
  /** One short instruction. What the operator does here, or what the screen is showing them. */
  instruction: string;
}

export const STEPS: readonly StepDefinition[] = [
  { id: 1, name: 'Connect', instruction: 'The runtime has to reach the robot before a cycle can be commanded.' },
  { id: 2, name: 'Observe', instruction: 'Take one look at the scene. Nothing moves.' },
  { id: 3, name: 'Run', instruction: 'One press runs the whole cycle: align, grasp, lift, carry, place, return.' },
  { id: 4, name: 'Result', instruction: 'What this run did, and what was measured. The demonstration ends here.' },
  { id: 5, name: 'Cake live update', instruction: 'Later: it will update an active module while the robot continues its task.' },
];

export type StepState = 'done' | 'current' | 'blocked' | 'waiting' | 'later';

export interface StepView extends StepDefinition {
  state: StepState;
  /** What is stopping this step, in the runtime's own words where it supplied any. Null when nothing is. */
  blocker: string | null;
}

/**
 * WHICH RUN THE SCREEN IS LOOKING AT.
 *
 * The run in progress when there is one; otherwise the run this fold last saw finish. Null before either exists, which
 * is a console that has not seen a run at all and is not a run with no id.
 */
export function currentRunId(view: ViewState): string | null {
  return view.active_run?.run_id ?? view.outcome.run_id ?? null;
}

/**
 * Whether the fold's outcome is the end of the run this fold started, or of a run after it. False when the fold started a
 * run (its `run.started` names it) and the outcome belongs to another that finished before that start: that outcome is a
 * finished earlier run, and nothing of the run in progress (its failure, its failed stage) may be read from it. True when
 * the fold names no run it started.
 *
 * An outcome of another run that finished AFTER the fold's `run.started` is the newest run's end. The fold missed that
 * run's records (a declared gap) and the snapshot's `last_outcome` carried its end, which is taken only when it finished
 * later (`reducer.ts`); hiding it would hide the newest run's failure.
 */
export function outcomeIsOfFoldedRun(view: Pick<ViewState, 'run_started' | 'outcome'>): boolean {
  const started = view.run_started;
  if (started === null || typeof started.run_id !== 'string' || view.outcome.run_id === started.run_id) return true;
  const finished = view.outcome.finished_seq;
  return typeof started.seq === 'number' && finished !== null && finished > started.seq;
}

/**
 * Whether the fold's outcome is the end of the run the screen is on: of the fold's own run, and of no run other than the
 * one the runtime reports in progress. False while a later run is in progress, and then nothing of that run (its failure,
 * its failed stage) may be drawn from the outcome.
 *
 * The runtime's `active_run` is read here because the fold alone cannot always tell. A tab that folded no `run.started`
 * of the run in progress (a reload, a second display: both subscribe from the snapshot's cursor) holds the snapshot's
 * `last_outcome`, which is the run before it. And one tab can fold the snapshot naming a new run a moment before that
 * run's `run.started`, while its outcome is still the run before.
 */
export function outcomeIsOfRunOnScreen(view: ViewState): boolean {
  const active = view.active_run?.run_id;
  if (typeof active === 'string' && view.outcome.run_id !== active) return false;
  return outcomeIsOfFoldedRun(view);
}

/**
 * The result OF THE RUN THE SCREEN IS LOOKING AT, or null when that run has not reported one.
 *
 * A run in progress has no result, and this returns null for it rather than the previous run's. That is the whole
 * point of the function: the alternative is the last success standing in for a run that has not finished, and after
 * the next failure it would be a success standing in for a failure.
 */
export function resultForCurrentRun(view: ViewState): OutcomeView | null {
  const runId = currentRunId(view);
  if (runId === null) return null;
  if (view.outcome.run_id !== runId) return null;
  if (view.outcome.reported === null && view.outcome.finished_seq === null) return null;
  return view.outcome;
}

/**
 * The run BEFORE the one on the screen, when the runtime reported one and it is a different run.
 *
 * It may be drawn, clearly labelled as the previous run and with the time it finished. It never supplies the current
 * run's outcome, its predicates or its failure.
 */
export function previousRun(view: ViewState): OutcomeView | null {
  const previous = view.previous_outcome;
  if (previous === null || previous.run_id === null) return null;
  return previous.run_id === currentRunId(view) ? null : previous;
}

/** True while the runtime reports a run in progress, or while the fold is inside one that has not reported an end. */
export function runIsActive(view: ViewState): boolean {
  // A RECORD OF THE END BEATS A SNAPSHOT THAT STILL SHOWS IT RUNNING. The snapshot's active run is a photograph taken
  // at the runtime's cursor and the 5 s poll is behind the stream, so the `run.finished` record for THAT run is the
  // newer fact. It is matched by run id, so the end of the PREVIOUS run can never end this one.
  if (resultForCurrentRun(view) !== null) return false;
  if (view.active_run !== null) return true;
  const machine = view.machine;
  if (machine === null) return false;
  return !isTerminalState(machine.state);
}

/** True once the controller has captured anything of the scene in this session. */
export function sceneWasObserved(view: ViewState): boolean {
  if (view.decision_frame !== null || view.last_perception !== null) return true;
  return Object.values(view.observations).some((item) => item.media === 'frame' || item.media === 'frame_pair');
}

/**
 * WHAT IS STOPPING A CYCLE, in the runtime's own words.
 *
 * The runtime publishes the answer on the `start_cycle` capability: a state, a reason code, a sentence and the
 * dependency that caused it. This reads that and adds nothing to it except the one thing the runtime cannot see, which
 * is a snapshot that stopped arriving and left an old answer on the screen.
 *
 * `unknown` IS NEVER DRAWN AS READY. A capability the runtime could not determine is a capability nobody has said yes
 * to, and the two reason codes that arrive with it (`robot_readiness_not_read`, `robot_readiness_stale`) are both
 * "nothing is known", which is not the same fact as "it is fine".
 */
export interface Readiness {
  ready: boolean;
  /** The blocker in a short line for the step guide. Null only when a cycle may actually be commanded. */
  blocker: string | null;
  /** The runtime's own reason code, when it sent one. Shown beside the sentence, never instead of it. */
  code: string | null;
  /** The longer consequence, for the panel under the guide. Null when there is nothing more to say than the blocker. */
  detail: string | null;
}

export function readiness(view: ViewState, nowMs: number, replaying: boolean): Readiness {
  if (replaying) {
    return { ready: false, blocker: 'a recorded run is on the screen, so nothing here commands the robot', code: null, detail: null };
  }
  if (!view.contract_compatible) {
    return { ready: false, blocker: 'the runtime speaks a contract version this console does not read', code: view.read_only_reason, detail: null };
  }
  if (!view.commands_enabled) {
    return { ready: false, blocker: 'the server does not allow this tab to command', code: view.read_only_reason, detail: null };
  }
  if (view.unresolved !== null) {
    return {
      ready: false,
      blocker: 'one command’s execution is not known, and it blocks conflicting work until it is resolved',
      code: view.unresolved.block_reason_code,
      detail: null,
    };
  }

  const capability = view.capabilities.find((item) => item.id === 'start_cycle') ?? null;
  if (capability === null) {
    return { ready: false, blocker: 'this runtime does not report a control that starts a cycle', code: null, detail: null };
  }

  // The robot reading, which is what turns the four "the robot computer answered" codes and the two "nobody has said
  // anything" codes into six different sentences with six different next actions.
  const robot = robotReading(view, nowMs);
  if (capability.state !== 'enabled') {
    return {
      ready: false,
      blocker: capability.reason ?? robot.headline,
      code: capability.reason_code,
      detail: robot.code === capability.reason_code ? robot.consequence : null,
    };
  }
  if (robot.state === 'answer_not_current') {
    return { ready: false, blocker: 'the answer about the robot is not current', code: null, detail: robot.consequence };
  }
  return { ready: true, blocker: null, code: null, detail: robot.state === 'host_running' ? robot.consequence : null };
}

/**
 * The step the console is on, derived from runtime state and from nothing an operator clicked.
 *
 * THE GUIDE IS SCOPED TO THE DEMONSTRATION, NOT TO WHICHEVER CONTROLLER SUBPROCESS LAST EXITED. The five steps
 * describe one thing: connect, look, run the cycle, read the result. An Observe and a return of the arm are other
 * commands that run through the same supervisor and end with the same kind of record, and reading the end of one of
 * them as the end of the CYCLE is what marked Run done and drew the result of a cycle nobody ran. `scope` says which
 * command the record names, and only a command that completes the Run step may move this past it: see
 * `model/command.ts`, where that is decided from `command_type` and from nothing else.
 *
 * A COMMAND WITH NO PLACEMENT IN IT NEVER REACHES STEP 4. A successful Observe leaves the console where an Observe
 * leaves it, which is with the scene looked at and Run as the next thing to press.
 */
export function currentStep(view: ViewState, ready: boolean, scope: CommandScope = UNSCOPED_COMMAND): StepId {
  if (runIsActive(view)) return 3;
  if (resultForCurrentRun(view) !== null && scope.completes_run) return 4;
  if (!ready) return 1;
  return sceneWasObserved(view) ? 3 : 2;
}

export function stepViews(view: ViewState, ready: boolean, blocker: string | null, scope: CommandScope = UNSCOPED_COMMAND): StepView[] {
  const current = currentStep(view, ready, scope);
  return STEPS.map((step) => {
    if (step.id === 5) return { ...step, state: 'later' as StepState, blocker: null };
    if (step.id === current) {
      return { ...step, state: step.id === 1 && !ready ? ('blocked' as StepState) : ('current' as StepState), blocker: step.id === 1 ? blocker : null };
    }
    return { ...step, state: step.id < current ? ('done' as StepState) : ('waiting' as StepState), blocker: null };
  });
}

/* ---- what the controller is doing, in one phrase ----------------------------------------------------------------- */

/**
 * ONE PHRASE FOR WHAT IS HAPPENING, derived from the controller's own state.
 *
 * The phase table this replaces drew six rows and a status word for each of them, which is a report on the run rather
 * than an answer to the question somebody standing at the robot is asking. A state this console does not know is
 * printed as the controller wrote it, never mapped to the nearest one that is known.
 */
export interface CurrentAction {
  phrase: string;
  /** One short explanation, only where it adds something the phrase does not carry. Null otherwise. */
  detail: string | null;
  /** The controller's own state name, for the operator who wants it. Null before any state was recorded. */
  state: string | null;
}

const PHASE_PHRASE: Record<string, string> = {
  locate: 'Looking for the cube',
  align: 'Aligning with cube',
  pick: 'Closing the gripper',
  verify_grip: 'Checking grip',
  place: 'Placing the cube',
  return: 'Returning to start',
};

export function currentAction(view: ViewState, ready: boolean, scope: CommandScope = UNSCOPED_COMMAND): CurrentAction {
  const machine = view.machine;
  const action = view.current_action;

  // A COMMAND THAT IS NOT A CYCLE SAYS WHAT IT DID. "Run finished" over a non-motion Observe is the same mistake the
  // step guide made: it names the cycle, which is not what ran. The words come from the command type on the record.
  const finishedPhrase = scope.kind === 'observe' ? 'Observation captured' : scope.kind === 'return_arm' ? 'Arm returned' : 'Run finished';

  if (machine === null) {
    const result = resultForCurrentRun(view);
    if (result !== null) return { phrase: finishedPhrase, detail: null, state: null };
    return {
      phrase: ready ? 'Idle, ready to run' : 'Idle',
      detail: ready ? null : 'no cycle has been commanded in this runtime session',
      state: null,
    };
  }

  if (isTerminalState(machine.state)) {
    return { phrase: finishedPhrase, detail: null, state: machine.state };
  }

  // THE SWEEP AND THE SELECTION SAY WHAT THE SEARCH FOUND. Both are still the search (the rail chip stays on Find),
  // and the fields of the controller's own [STATE] line are the sector being looked through and the mode the block
  // measured, which is what somebody watching the one-block demo is waiting to read.
  if (machine.state === 'SEARCH_SWEEP') {
    return { phrase: 'Scanning for the block', detail: sweepWords(stateFields(view, 'SEARCH_SWEEP')), state: machine.state };
  }
  if (machine.state === 'TARGET_SELECTED') {
    return { phrase: 'Block found, placement measured', detail: selectionWords(stateFields(view, 'TARGET_SELECTED')), state: machine.state };
  }
  // THE ADDITIVE PLACEMENT POLICIES SAY WHICH ONE IS PLACING THE CUBE, AND HOW (autonomy/manipulation/
  // placement_executor.py): the choice once the cube is held, the next policy the run goes on with, and each measured
  // carry step, from the controller's own state lines.
  const placing = placingWords(view, machine.state);
  if (placing !== null) return { phrase: PHASE_PHRASE['place'] ?? 'Placing the cube', detail: placing, state: machine.state };

  const phase = phaseOfState(machine.state);
  const phrase = phase === 'unknown' || phase === 'terminal' ? machine.state : PHASE_PHRASE[phase] ?? machine.state;
  // The action record says what the controller is executing right now, which is finer than the phase and is the thing
  // that moves while the phase stands still. It is only a detail when one is actually running.
  const detail = action !== null && action.phase === 'started' ? `executing ${action.action_type}` : null;
  return { phrase, detail, state: machine.state };
}

/**
 * THE FIELDS OF THE CONTROLLER'S OWN STATE LINE, for one state name: the newest `[STATE] <name> {...}` diagnostic this
 * fold holds, parsed, or null when it holds none that parses.
 *
 * The contract's state.transition carries machine, from_state and to_state and nothing else; the fields a state was
 * raised with (the sector of a sweep, the mode a target measured) travel as ONE diagnostic.text record with the same
 * text the operator saw at the terminal (bin/laptop/manipulation/run_single_cube_cycle.py, "the fields have no other
 * place in the contract"). This reads that line and nothing else: a line that does not parse is not a field record.
 */
export function stateFields(view: ViewState, state: string): Record<string, unknown> | null {
  const prefix = `[STATE] ${state} `;
  for (let index = view.diagnostics.length - 1; index >= 0; index -= 1) {
    const line = view.diagnostics[index];
    if (line === undefined || !line.text.startsWith(prefix)) continue;
    try {
      const parsed: unknown = JSON.parse(line.text.slice(prefix.length));
      return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** One field of a state line as text, under the first of its names the line carries; null when it carries none. */
function fieldText(fields: Record<string, unknown> | null, names: readonly string[]): string | null {
  if (fields === null) return null;
  for (const name of names) {
    const value = fields[name];
    if (typeof value === 'string' && value.length > 0) return value;
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return null;
}

/** The sector, the pan and the outcome of a sweep step, each only as the line states it. */
function sweepWords(fields: Record<string, unknown> | null): string {
  const parts: string[] = [];
  const sector = fieldText(fields, ['sector', 'sector_name', 'sector_index']);
  if (sector !== null) parts.push(`sector ${sector}`);
  const pan = fieldText(fields, ['pan_deg', 'pan']);
  if (pan !== null) parts.push(`pan ${pan} deg`);
  const outcome = fieldText(fields, ['outcome']);
  if (outcome !== null) parts.push(`outcome ${outcome}`);
  return parts.length === 0 ? 'the sector is not stated in the records this tab holds' : parts.join(', ');
}

/** What the measured mode of the selected block means for the demo: the arm alone, or the base; and, under the additive
 * placement words, the policy chosen for it and its plan (placementPlanWords). */
function selectionWords(fields: Record<string, unknown> | null): string | null {
  const mode = fieldText(fields, ['mode', 'measured_mode']);
  if (mode === null) return null;
  const plan = placementPlanWords(fields === null ? null : fields['placement_plan']);
  const chosen = plan === null ? '' : `; the placement that moves the base least is ${plan}`;
  if (mode === 'BASE_ADVANCE') return `it measured ${mode}: placing it needs the base to move${chosen}`;
  if (mode === 'ARM_ONLY') return `it measured ${mode}: the arm alone places it, no wheels${chosen}`;
  return `it measured ${mode}${chosen}`;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** A length as the record states it: whole millimetres when it is whole, else to 0.1 mm. */
function lengthMm(value: number): string {
  return `${Number.isInteger(value) ? String(value) : value.toFixed(1)} mm`;
}

/**
 * ONE PLACEMENT POLICY'S PLAN IN WORDS, from the plan record the controller states (placement_policy.Plan.record: policy,
 * base_advance_mm, fk_travel_mm, arm_out_mm). The policy's own name leads, then the base motion and the arm's carry, each
 * only as the record states it. Null when the record carries no plan.
 */
export function placementPlanWords(plan: unknown): string | null {
  if (typeof plan !== 'object' || plan === null || Array.isArray(plan)) return null;
  const record = plan as Record<string, unknown>;
  const policy = typeof record['policy'] === 'string' ? record['policy'] : null;
  if (policy === null) return null;
  const advance = finite(record['base_advance_mm']);
  const travel = finite(record['fk_travel_mm']);
  const out = finite(record['arm_out_mm']);
  const parts: string[] = [];
  if (advance !== null) parts.push(advance > 0 ? `a measured base advance of ${lengthMm(advance)}` : 'no base motion');
  if (travel !== null && travel > 0) {
    parts.push(`the arm carries the block ${lengthMm(travel)}${out === null ? '' : ` to ${lengthMm(out)} beyond the grasp radius`}`);
  } else if (advance !== null && advance > 0) {
    parts.push('the release at about the grasp radius');
  }
  return parts.length === 0 ? policy : `${policy}, ${parts.join(', then ')}`;
}

/** The detail of the current action while an additive placement policy places the cube, from its own state line. */
function placingWords(view: ViewState, state: string): string | null {
  if (state === 'PLACEMENT_POLICY') {
    const fields = stateFields(view, state);
    const plan = placementPlanWords(fields === null ? null : fields['plan']);
    return plan === null ? 'no placement policy of this run places the block from the measured grasp' : `placing it with ${plan}`;
  }
  if (state === 'PLACEMENT_FALLBACK') {
    const fields = stateFields(view, state);
    const from = fieldText(fields, ['from_policy']);
    const plan = placementPlanWords(fields === null ? null : fields['plan']);
    const why = fieldText(fields, ['why']);
    const failed = `${from ?? 'the last policy'} did not place the block${why === null ? '' : ` (${why})`}`;
    return plan === null ? `${failed}, and no policy is left that places it from here` : `${failed}; going on with ${plan}`;
  }
  if (state === 'REACH_CARRY') {
    const fields = stateFields(view, state);
    const commanded = finite(fields === null ? null : fields['commanded_out_mm']);
    const measured = finite(fields === null ? null : fields['measured_out_mm']);
    const step = finite(fields === null ? null : fields['step']);
    if (commanded === null || measured === null) return 'the carry step is not stated in the records this tab holds';
    return `carry step ${step === null ? '' : `${String(step)} `}commanded ${lengthMm(commanded)} out, measured ${lengthMm(measured)} out`;
  }
  return null;
}

/* ---- staleness that advances on its own -------------------------------------------------------------------------- */

/**
 * HOW OLD THE NEWEST TELEMETRY LINE IS, in this browser's clock.
 *
 * An open connection is not fresh telemetry. This is measured against the ARRIVAL of the last line of any slot, so it
 * keeps growing when the lines stop, which is exactly the case a connection state cannot see. Null when no line has
 * ever arrived, which is not an age of zero.
 */
export function telemetryAgeMs(
  telemetry: { tap_received_at_ms: number | null; preview_received_at_ms: number | null },
  nowMs: number,
): number | null {
  const last = Math.max(telemetry.preview_received_at_ms ?? 0, telemetry.tap_received_at_ms ?? 0);
  if (last === 0) return null;
  // The shared tick is read once a second and a line can arrive between two ticks, so the subtraction of two
  // readings of the SAME clock can come out below zero. A negative age is not a fact about anything; it is a line
  // that arrived after the tick this render is using, and the honest reading of it is "just now".
  return Math.max(0, nowMs - last);
}

/** How long a telemetry slot may be silent before the screen stops presenting its last sample as current. */
export const TELEMETRY_SILENT_AFTER_MS = 4000;

export function telemetryIsCurrent(ageMs: number | null): boolean {
  return freshness(ageMs, TELEMETRY_SILENT_AFTER_MS) === 'fresh';
}
