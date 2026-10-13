// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The six phases the screen groups a mission into, decided in ONE place.
//
// The controller has 25 state names and the screen has 6 groups, so the grouping is a presentation decision taken over
// a controller enum. It was being taken twice, in two panels, with two different lists, and neither list was complete.
// It is taken in one place now, with every state named explicitly in the stage file of the stage that emits it
// (./stages) and read here: a state no stage file names is reported as unknown and rendered as itself, never silently
// dropped into a group it was never assigned to.
//
// Three things about the mission are not states at all, and each one is handled here rather than guessed at by a panel:
//
//   THE ARM RETURN HAS NO STATE. The controller returns the arm to the low scan pose with an action carrying the label
//   "EXIT to the low scan pose" (bin/laptop/manipulation/run_single_cube_cycle.py:903) and emits no state for it. The
//   Return group is therefore derived from BASE_RETURN and BASE_RETURNED, from that action label, and from the
//   base_returned predicate, and not from states alone.
//
//   THE VERIFICATION LIFTS HAVE NO STATES. The three lifts are actions labelled "VERIFICATION lift", "CLEARANCE lift"
//   and "CONFIRM lift" (:1050 to :1052); OBJECT_SECURED is emitted once afterwards with the retention score (:1057).
//   Verify grip folds actions, not a state sequence.
//
//   DONE_ALREADY_PLACED IS TERMINAL AND A NO-OP. It is emitted when the cube was already inside the pad outline at the
//   start (:952), and the run then reports already_placed_noop as a success with placed set by fiat rather than by a
//   placement measurement. It is not a phase and this file never returns it as one.
//
// Entry is not completion. A phase that was entered says so and says no more than that: it is called verified only
// when the predicate that decides it measured true, and failed only when that predicate measured false. A retry shows
// an attempt count taken from the records; nothing here computes a percentage, and nothing here resets the mission.

import { predicateState } from './predicate';
import { STAGE_STATES } from './stages/index';
import type { ActionView, ViewState } from './types';
import { activeRunOnRecord } from './selectedRun';

export type Phase = 'locate' | 'align' | 'pick' | 'verify_grip' | 'place' | 'return';

/** The phases in the order the mission passes through them. */
export const PHASES: readonly Phase[] = ['locate', 'align', 'pick', 'verify_grip', 'place', 'return'];

export const PHASE_LABEL: Record<Phase, string> = {
  locate: 'Locate',
  align: 'Align',
  pick: 'Pick',
  verify_grip: 'Verify grip',
  place: 'Place',
  return: 'Return',
};

/**
 * Where a state name lands.
 *
 * `terminal` is the end that is not a phase, and `unknown` is a state name this console has never been told about,
 * which is what a newer controller will look like from here.
 */
export type PhaseAssignment = Phase | 'terminal' | 'unknown';

/**
 * Every state the controller emits, with the group it is shown in.
 *
 * The entries live in the stage files (./stages), each state beside its sentence and with the reason it belongs where
 * it does; the line numbers there are the `self.state(...)` call sites in bin/laptop/manipulation/run_single_cube_cycle.py.
 * This is the one table built from them.
 */
const STATE_PHASE: Readonly<Record<string, Phase | 'terminal'>> = Object.fromEntries(
  Object.entries(STAGE_STATES).map(([state, entry]) => [state, entry.phase]),
);

/**
 * The 25 state names this console knows, which is every name the controller emits today.
 *
 * Nothing in the contract pins these: the state.transition payload carries `to_state` as a bare id with no enum, so
 * this list and the test over it are the only place a controller rename would be caught.
 */
export const CONTROLLER_STATES: readonly string[] = Object.keys(STATE_PHASE);

export function phaseOfState(state: string): PhaseAssignment {
  return STATE_PHASE[state] ?? 'unknown';
}

/* ---- the five steps the imported design presents ----------------------------------------------------------------- */

/**
 * THE DESIGN'S FIVE STEPS, over the controller's own twenty-five states.
 *
 * The imported design presents Find, Pick, Carry, Place and Return. The controller has six phases and does not have a
 * Carry phase: carrying, planning the placement and driving the base are all inside `place`, because they exist only
 * to place the cube. So the split the design draws is finer than the phase table in one spot and coarser in another,
 * and it is a PRESENTATION mapping over the same state names rather than a second opinion about them.
 *
 * NOTHING HERE CHANGES THE STATE MACHINE. Every state's step is written beside its phase in its stage file (./stages),
 * so `STATE_PHASE` above stays the one list of controller states this console has; a state missing from that list is
 * unknown to both and is rendered as itself. The
 * controller's own phase and state names stay available in Diagnostics, where somebody debugging reads them.
 */
export type PresentationStep = 'find' | 'pick' | 'carry' | 'place' | 'return';

export const PRESENTATION_STEPS: readonly PresentationStep[] = ['find', 'pick', 'carry', 'place', 'return'];

export const PRESENTATION_STEP_LABEL: Record<PresentationStep, string> = {
  find: 'Find',
  pick: 'Pick',
  carry: 'Carry',
  place: 'Place',
  return: 'Return',
};

/**
 * Which of the five a state belongs to, read from the stage files (a terminal state has no step).
 *
 * The two that are not a straight rename of a phase:
 *   CARRY holds the states that move the cube toward the placement: the plan, the observation the plan rests on, the
 *   arm carry and its reach limit, the finding that the arm alone cannot reach, and the base measurement and advance.
 *   PLACE holds the release decision, the release itself and the measurement of where the cube ended up.
 */
const STATE_PRESENTATION: Readonly<Record<string, PresentationStep>> = Object.fromEntries(
  Object.entries(STAGE_STATES).flatMap(([state, entry]) => (entry.step === null ? [] : [[state, entry.step]])),
);

export function presentationStepOfState(state: string): PresentationStep | 'terminal' | 'unknown' {
  if (STATE_PHASE[state] === 'terminal') return 'terminal';
  return STATE_PRESENTATION[state] ?? 'unknown';
}

/**
 * THE STEP OF A STATE WHERE IT WAS ENTERED. BASE_MEASURED (and the controller's base_measured predicate) is the
 * measurement after EVERY base pulse: the controller enters it inside measured_base_pulse, which the advance and the
 * return both call (`run_single_cube_cycle.py`). One that follows a BASE_RETURN belongs to Return, not to Carry; one that
 * follows a BASE_ADVANCE, or no pulse at all, stays on Carry. `before` is the states entered before it, oldest first.
 */
export const PULSE_MEASUREMENT_STATE = 'BASE_MEASURED';

export function pulseStepBefore(before: readonly string[]): 'carry' | 'return' {
  for (let index = before.length - 1; index >= 0; index -= 1) {
    const state = before[index];
    if (state === 'BASE_RETURN' || state === 'BASE_RETURNED') return 'return';
    if (state === 'BASE_ADVANCE') return 'carry';
  }
  return 'carry';
}

export function presentationStepInContext(state: string, before: readonly string[]): PresentationStep | 'terminal' | 'unknown' {
  if (state === PULSE_MEASUREMENT_STATE) return pulseStepBefore(before);
  return presentationStepOfState(state);
}

/** The predicate whose verdict decides each step, where one does. `carry` has none: nothing measures a carry. */
export const PRESENTATION_STEP_PREDICATE: Partial<Record<PresentationStep, string>> = {
  find: 'cube_seen_with_a_contact_point',
  pick: 'object_secured',
  place: 'placed',
  return: 'base_returned',
};

/** True for the one state that ends a run without a phase having happened. */
export function isTerminalState(state: string): boolean {
  return STATE_PHASE[state] === 'terminal';
}

/**
 * The one predicate whose verdict decides each phase, with the call site that emits it.
 *
 * A phase is never called verified because the mission moved past it. It is called verified when the controller
 * recorded a verdict for it and that verdict is true.
 *
 * locate is the asymmetric one: cube_seen_with_a_contact_point (:939) is recorded only when the search FAILED, so
 * locate can be reported failed but can never be reported verified. That is what the record supports, and inventing a
 * positive verdict for it would be inventing a measurement.
 */
const PHASE_PREDICATE: Record<Phase, string> = {
  locate: 'cube_seen_with_a_contact_point',
  align: 'aligned',
  pick: 'closed_on_something',
  verify_grip: 'object_secured',
  place: 'placed',
  return: 'base_returned',
};

/**
 * Work the controller records as an ACTION LABEL and never as a state. Without these two entries the screen would say
 * a phase had not been reached while the arm was in the middle of it.
 */
const PHASE_ACTION_LABELS: Partial<Record<Phase, readonly string[]>> = {
  verify_grip: ['VERIFICATION lift', 'CLEARANCE lift', 'CONFIRM lift'],
  return: ['EXIT to the low scan pose'],
};

/**
 * The state the controller records once per attempt, for the two phases where it does.
 *
 * ALIGN is emitted once per alignment attempt (:988, carrying `attempt`), and GRASP_CLOSE once per close (:1037,
 * carrying `attempt`). Every other phase records no per-attempt state, and for those the attempt count is null rather
 * than 1: a count of something nobody counted is not a fact.
 */
const PHASE_ATTEMPT_STATE: Partial<Record<Phase, string>> = {
  align: 'ALIGN',
  pick: 'GRASP_CLOSE',
};

/**
 * `stopped_by_controller`, `stopped_on_request`, `refused` and `no_outcome` are the stage where a run ENDED, in the run's
 * own word (docs/autonomous-demo/console-redesign-2026-09-27.md, section 5.1): a stop is never drawn as a failure.
 */
export type PhaseStatus =
  | 'not_reached'
  | 'not_observed'
  | 'active'
  | 'entered'
  | 'verified'
  | 'failed'
  | 'stopped_by_controller'
  | 'stopped_on_request'
  | 'refused'
  | 'no_outcome';

/** What each status says on the screen, so no panel writes its own word for it. */
export const PHASE_STATUS_WORDS: Record<PhaseStatus, string> = {
  not_reached: 'Not yet',
  not_observed: 'Not seen from here',
  active: 'In progress',
  entered: 'Entered',
  verified: 'Verified',
  failed: 'Failed',
  stopped_by_controller: 'Stopped here by the controller',
  stopped_on_request: 'Stopped here on request',
  refused: 'Refused here',
  no_outcome: 'Ended here with no stated outcome',
};

export interface PhaseRow {
  phase: Phase;
  label: string;
  status: PhaseStatus;
  /** The status in words. It is here so that the phrase lives in one place and not in every panel that draws a row. */
  words: string;
  /** The mission sequence number this phase was first entered at, or null when it was never entered. */
  entered_at_seq: number | null;
  /** The recorded verdict for this phase, or null when nothing has decided it yet. */
  decided_by: { predicate: string; value: boolean; seq: number } | null;
  /**
   * How many attempts the records carry for this phase, or null where the controller records no per-attempt state.
   *
   * It counts the transitions kept in `machine.history`, which the reducer bounds, so on a very long run it is a lower
   * bound rather than a total. It is never turned into a percentage and it never resets anything.
   */
  attempts: number | null;
  /** The records this row was built from, in the same form the reason sentence uses, so a reader can go and check it. */
  basis: string[];
}

/**
 * What the rows are built from: the folded view, or the four of its fields that decide them.
 *
 * `active_run` is here because a phase is in progress only while a RUN is. The machine keeps its last state after a
 * run ends, and a row that read that state alone would go on saying the last phase was under way long after the robot
 * stopped.
 */
export type PhaseInput = Pick<ViewState, 'machine' | 'predicates' | 'action_history' | 'active_run' | 'run_started'> & {
  /**
   * The fold's outcome, which is what says a run's end has been recorded. Absent in an input that carries only the
   * five fields above; the snapshot's active run alone then decides, as it always did.
   */
  outcome?: ViewState['outcome'];
};

/**
 * THE RUN THE ROWS MAY CALL IN PROGRESS: the card's rule (`selectedRun.activeRunOnRecord`, redesign r4 verify-r2 must_fix
 * 2), so a run whose `run.finished` the fold holds is not in progress however long the snapshot keeps naming it, and a
 * newer `run.started` wins over an older snapshot. Null when no run is in progress by that rule.
 */
export function runInProgressForRows(view: PhaseInput): string | null {
  if (view.outcome === undefined) return view.active_run?.run_id ?? null;
  return activeRunOnRecord({ active_run: view.active_run, run_started: view.run_started, outcome: view.outcome });
}

export interface PhaseProgressView {
  rows: PhaseRow[];
  /**
   * The phase the controller is in now. Null when it is in none, which is any of: no run is in progress, the run
   * ended, the state is the terminal no-op, or the state is one this console has no phase for.
   */
  current: Phase | null;
  /** The terminal no-op, when the run reached it. It is not a phase and is never drawn as one. */
  terminal: { state: string; seq: number } | null;
  /** States this fold recorded that this console has no phase for, first occurrence first. */
  unmapped: { state: string; seq: number }[];
}

function actionLabel(action: ActionView): string | null {
  const parameter = action.parameters.find((item) => item.name === 'label');
  if (parameter === undefined || parameter.value === null) return null;
  return String(parameter.value);
}

/**
 * The six phase rows, built from the states the controller recorded, the actions it labelled and the verdicts it
 * measured. Nothing is derived from the clock and nothing is derived from the outcome word.
 */
export function phaseProgress(view: PhaseInput): PhaseProgressView {
  const machine = view.machine;
  const predicates = view.predicates;
  const actions = view.action_history;
  const running = runInProgressForRows(view) !== null;
  const history = machine === null ? [] : machine.history;

  // A tab that opened after the run had already started never saw the transitions that came before its cursor: the
  // snapshot carries the state the controller is in now, not how it got there. Saying "Not yet" about a phase this
  // run walked through before anyone was watching is a claim about the robot made out of this tab's own ignorance.
  // run_started is folded from the run.started record, so its absence under an active run is exactly that case.
  const joinedMidRun = running && view.run_started === null;

  const currentAssignment = machine === null || !running ? null : phaseOfState(machine.state);
  const current =
    currentAssignment === null || currentAssignment === 'terminal' || currentAssignment === 'unknown' ? null : currentAssignment;

  const enteredAt = new Map<Phase, { seq: number; state: string }>();
  const stateCounts = new Map<string, number>();
  const unmapped: { state: string; seq: number }[] = [];
  const unmappedSeen = new Set<string>();
  let terminal: { state: string; seq: number } | null = null;

  for (const item of history) {
    stateCounts.set(item.state, (stateCounts.get(item.state) ?? 0) + 1);
    const assignment = phaseOfState(item.state);
    if (assignment === 'terminal') {
      terminal = { state: item.state, seq: item.seq };
      continue;
    }
    if (assignment === 'unknown') {
      if (!unmappedSeen.has(item.state)) {
        unmappedSeen.add(item.state);
        unmapped.push({ state: item.state, seq: item.seq });
      }
      continue;
    }
    if (!enteredAt.has(assignment)) enteredAt.set(assignment, { seq: item.seq, state: item.state });
  }

  // The current state is taken from the machine as well as from the history: a fold whose history was truncated, or a
  // snapshot that carried a state without its transitions, must still show the phase the controller is in now.
  if (machine !== null) {
    const assignment = phaseOfState(machine.state);
    if (assignment === 'terminal') terminal = terminal ?? { state: machine.state, seq: machine.since_seq };
    else if (assignment === 'unknown') {
      if (!unmappedSeen.has(machine.state)) {
        unmappedSeen.add(machine.state);
        unmapped.push({ state: machine.state, seq: machine.since_seq });
      }
    } else if (!enteredAt.has(assignment)) enteredAt.set(assignment, { seq: machine.since_seq, state: machine.state });
  }

  const rows = PHASES.map((phase) => {
    const basis: string[] = [];
    const entry = enteredAt.get(phase) ?? null;
    let enteredSeq = entry === null ? null : entry.seq;
    if (entry !== null) basis.push(`state.transition to ${entry.state} at seq ${String(entry.seq)}`);

    // Work that exists only as an action label enters the phase too, and says which label it was entered by.
    for (const label of PHASE_ACTION_LABELS[phase] ?? []) {
      const action = actions.find((item) => actionLabel(item) === label);
      if (action === undefined) continue;
      basis.push(`action ${action.action_type} labelled ${label} at seq ${String(action.seq)}`);
      enteredSeq = enteredSeq === null ? action.seq : Math.min(enteredSeq, action.seq);
    }

    const record = predicates.find((item) => item.predicate === PHASE_PREDICATE[phase]) ?? null;
    const decided =
      record !== null && predicateState(record) === 'evaluated' && record.value !== null
        ? { predicate: record.predicate, value: record.value, seq: record.seq }
        : null;
    if (decided !== null) basis.push(`predicate.evaluated ${decided.predicate} at seq ${String(decided.seq)}`);

    const attemptState = PHASE_ATTEMPT_STATE[phase];
    const attempts = attemptState === undefined ? null : stateCounts.get(attemptState) ?? 0;
    if (attemptState !== undefined && attempts !== null && attempts > 1) {
      basis.push(`${String(attempts)} x state.transition to ${attemptState}`);
    }

    // The order matters. A phase the controller is in now, in a run that is still going, is in progress, whatever an
    // earlier verdict said about it: an alignment that failed once and is being retried is not a failed alignment. A
    // verdict outranks entry, because the controller can record one without ever entering the phase: base_returned is
    // recorded true with no BASE_RETURN state at all when the base never advanced (:1437).
    let status: PhaseStatus;
    if (current === phase) status = 'active';
    else if (decided !== null) status = decided.value ? 'verified' : 'failed';
    else if (enteredSeq !== null) status = 'entered';
    else if (joinedMidRun) status = 'not_observed';
    else status = 'not_reached';

    return {
      phase,
      label: PHASE_LABEL[phase],
      status,
      words: PHASE_STATUS_WORDS[status],
      entered_at_seq: enteredSeq,
      decided_by: decided,
      attempts,
      basis,
    };
  });

  return { rows, current, terminal, unmapped };
}
