// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FIVE STEPS THE IMPORTED DESIGN PRESENTS: Find, Pick, Carry, Place, Return.
//
// It is the same reasoning `model/phase.ts` does for the six-phase table, over the same state names and the same
// predicates, grouped the way the design groups them. `model/phase.ts` owns the state list and the grouping; this
// builds the rows. The controller's own phase names and its twenty-three state names are not replaced by these five:
// they stay on the screen in Diagnostics, which is where somebody debugging looks for them.
//
// ENTRY IS NOT COMPLETION, and a verdict outranks entry, exactly as the phase table has it. A step is verified only
// when the predicate that decides it measured true, failed only when that predicate measured false, and `carry` can be
// neither: nothing in the record measures a carry, so it is only ever reached, active or not reached.
//
// WITH A MISSION THERE IS A SECOND QUESTION: which target the five steps are about. `missionRail` at the foot of this
// file answers it, and it is the only thing in this file that knows a mission exists. The rows themselves are built
// the same way whichever target they describe.

import {
  PRESENTATION_STEPS,
  PRESENTATION_STEP_LABEL,
  PRESENTATION_STEP_PREDICATE,
  PHASE_STATUS_WORDS,
  presentationStepInContext,
  runInProgressForRows,
} from '../model/phase';
import type { PhaseInput, PhaseStatus, PresentationStep } from '../model/phase';
import type { MissionRecord, MissionTargetRecord } from '../model/missionRecord';
import { activeTarget, targetOfRun } from '../model/missionView';
import { predicateState } from '../model/predicate';

export interface StepRow {
  step: PresentationStep;
  label: string;
  status: PhaseStatus;
  words: string;
  /** The records this row was built from, so a reader can go and check it. */
  basis: string[];
}

export interface StepProgress {
  rows: StepRow[];
  /** The step the controller is in now, or null when it is in none. */
  current: PresentationStep | null;
  /** States this fold recorded that this console has no step for, first occurrence first. */
  unmapped: { state: string; seq: number }[];
}

export function presentationSteps(view: PhaseInput, runOnScreen?: string | null): StepProgress {
  const machine = view.machine;
  // THE CARD'S RULE (redesign r4 verify-r2 must_fix 2): the run in progress by the newer record (`runInProgressForRows`),
  // so a run whose end the fold holds has no step in progress beside a card that says it ended, and a run.started the
  // stream delivered before the snapshot's next poll has (verdict item 6). With `runOnScreen`, only that run is.
  const onRecord = runInProgressForRows(view);
  const running = onRecord !== null && (runOnScreen === undefined || onRecord === runOnScreen);
  const history = machine === null ? [] : machine.history;

  // A tab that opened after the run started never saw the transitions before its cursor. Saying "Not yet" about a step
  // this run walked through before anyone was watching is a claim made out of this tab's own ignorance.
  const joinedMidRun = running && view.run_started === null;

  // A state is placed where it was entered: a pulse measurement after a return pulse is the Return's.
  const priorTo = (seq: number): string[] => history.filter((item) => item.seq < seq).map((item) => item.state);
  const assignment = machine === null || !running ? null : presentationStepInContext(machine.state, priorTo(machine.since_seq));
  const current = assignment === null || assignment === 'terminal' || assignment === 'unknown' ? null : assignment;

  const enteredAt = new Map<PresentationStep, number>();
  const unmapped: { state: string; seq: number }[] = [];
  const seen = new Set<string>();

  const note = (state: string, seq: number): void => {
    const where = presentationStepInContext(state, priorTo(seq));
    if (where === 'terminal') return;
    if (where === 'unknown') {
      if (!seen.has(state)) {
        seen.add(state);
        unmapped.push({ state, seq });
      }
      return;
    }
    if (!enteredAt.has(where)) enteredAt.set(where, seq);
  };

  for (const item of history) note(item.state, item.seq);
  // The current state as well as the history: a fold whose history was truncated, or a snapshot that carried a state
  // without its transitions, must still show the step the controller is in now.
  if (machine !== null) note(machine.state, machine.since_seq);

  const rows = PRESENTATION_STEPS.map((step) => {
    const basis: string[] = [];
    const entered = enteredAt.get(step) ?? null;
    if (entered !== null) basis.push(`entered at seq ${String(entered)}`);

    const name = PRESENTATION_STEP_PREDICATE[step];
    const record = name === undefined ? undefined : view.predicates.find((item) => item.predicate === name);
    const decided =
      record !== undefined && predicateState(record) === 'evaluated' && record.value !== null
        ? { predicate: record.predicate, value: record.value, seq: record.seq }
        : null;
    if (decided !== null) basis.push(`predicate.evaluated ${decided.predicate} at seq ${String(decided.seq)}`);

    let status: PhaseStatus;
    if (current === step) status = 'active';
    else if (decided !== null) status = decided.value ? 'verified' : 'failed';
    else if (entered !== null) status = 'entered';
    else if (joinedMidRun) status = 'not_observed';
    else status = 'not_reached';

    return { step, label: PRESENTATION_STEP_LABEL[step], status, words: PHASE_STATUS_WORDS[status], basis };
  });

  return { rows, current, unmapped };
}

/**
 * The five steps at ONE status, with the reason stated on every row.
 *
 * It exists for a target whose own records this tab is not holding. With two cubes carried one after the other the
 * live fold describes the run that is going on NOW, and drawing those five steps under the other cube's name would
 * attribute one cube's progress to the other. The honest rail for that case is five steps that say what this tab
 * actually knows, in the words the phase table already uses for exactly this: "Not yet" for a target that has not run,
 * "Not seen from here" for one whose run this tab did not watch.
 */
export function stepsWithStatus(status: PhaseStatus, basis: string): StepRow[] {
  return PRESENTATION_STEPS.map((step) => ({
    step,
    label: PRESENTATION_STEP_LABEL[step],
    status,
    words: PHASE_STATUS_WORDS[status],
    basis: [basis],
  }));
}

export interface MissionRailBinding {
  rows: StepRow[];
  /** The target these five steps are about, or null when no mission names one and the rail is the plain live fold. */
  target: MissionTargetRecord | null;
  /** True when the rows were folded from records the mission recorded against THIS target. */
  scoped: boolean;
  /** Why the rows are what they are, for the title attribute and for anybody checking the claim. */
  basis: string;
}

/**
 * THE FIVE STEPS, FOR THE TARGET THE MISSION IS ON.
 *
 * With no mission, or with a mission that names no target, this is exactly what the rail has always been: the live
 * fold's five steps, unchanged. With a mission it answers for the ACTIVE target, and it refuses to answer with another
 * target's records: two cubes are carried one after the other, so the fold on this tab is one target's progress and
 * drawing it under the other's name is the one mistake a two-target rail can make.
 *
 * `liveRunId` is the run the presentation selected (`model/selectedRun.ts`), which is the run this fold describes.
 */
export function missionRail(mission: MissionRecord | null, view: PhaseInput, liveRunId: string | null): MissionRailBinding {
  if (mission === null) {
    return {
      rows: presentationSteps(view).rows,
      target: null,
      scoped: true,
      basis: 'the records this tab folded for the run on the screen',
    };
  }
  // The active target first. A mission that has finished names no active target, and the rail then answers for
  // whichever target the run on the screen was recorded against, which is still that run's own target and not a guess.
  const target = activeTarget(mission) ?? targetOfRun(mission, liveRunId);
  if (target === null) {
    return {
      rows: presentationSteps(view).rows,
      target: null,
      scoped: true,
      basis: `mission ${mission.mission_id} names no target for the run on the screen, so these are that run's own folded records`,
    };
  }
  if (liveRunId !== null && target.runs.includes(liveRunId)) {
    return {
      rows: presentationSteps(view).rows,
      target,
      scoped: true,
      basis: `the folded records of run ${liveRunId}, which this mission lists under ${target.target_id}`,
    };
  }
  // NOT this target's run. Whatever the fold holds belongs to another target and is not drawn against this one.
  const ran = target.runs.length > 0;
  const basis = ran
    ? `${target.label} has ${String(target.runs.length)} run(s) in this mission and this tab is folding none of them; the records this tab holds belong to another target`
    : `this mission has recorded no run against ${target.label} yet`;
  return { rows: stepsWithStatus(ran ? 'not_observed' : 'not_reached', basis), target, scoped: false, basis };
}
