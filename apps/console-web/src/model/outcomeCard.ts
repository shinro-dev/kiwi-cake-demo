// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE OUTCOME CARD (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 3.2, 4.4, 5.7 and 6.2).
//
// One card for the mission, top left: the mission's word and its run's, each its own (C3); why the run ended, with the
// stop's own measurement joined by action id (C4); where the robot was left, READ from the attempt's stop state and the
// run's newest measured state, never asked (C6); the runtime's step list, exactly and in order, and nothing else (C5);
// the mission's start and its derived end (T5). An ending stays expanded until the operator acknowledges it on this
// display; the acknowledgement is this display's own record and is never sent to the runtime.

import type { ContractEvent } from '../contracts/envelope';
import type { HistoryEntry } from '../api/types';
import type { MissionRecord, MissionAttemptRecord, MissionTargetRecord } from './missionRecord';
import type { RobotCapability } from './robotStatus';
import type { Currency } from './currency';
import { missionEndTime, runOrdinal } from './missionTimeline';
import { missionSteps, nextStep } from './nextSteps';
import type { NextStep } from './nextSteps';
import { PRESENTATION_STEP_LABEL, presentationStepInContext } from './phase';
import type { PresentationStep } from './phase';
import { missionCheck } from './missionCheck';
import type { MissionCheck } from './missionCheck';
import { placementLimit } from './placementLimit';
import { foldSceneMap, placementLine } from './sceneMap';
import { recordedValue, stopCause } from './stopCause';
import { durationWords, parseUtcMs, utcDayOf, utcStamp } from './utcTime';
import type { UtcStamp } from './utcTime';
import { CAPABILITY_HOLD_CODES, missionEnded, missionVerdict, RUN_ENDED_READING, runVerdictOf } from './verdict';
import type { Verdict } from './verdict';

/** The storage key of this display's acknowledgements (rule 6.2.2). */
export const ACKNOWLEDGED_KEY = 'kiwi-console.acknowledged.v1';

/** One acknowledgement, as this display keeps it. `key` names the ending it acknowledged. */
export interface AckRecord {
  key: string;
  mission_id: string;
  run_id: string | null;
  attempt: number | null;
  verdict: string;
  acknowledged_at: string;
}

/** The refusal codes whose limit the record states in named values (a placement the binding refused). */
const PLACEMENT_LIMIT_CODES: ReadonlySet<string> = new Set(['mission_binding_candidate_not_placeable', 'search_placement_infeasible', 'placement_infeasible_while_held']);

export interface OutcomeInput {
  mission: MissionRecord | null;
  missionError: string | null;
  history: readonly HistoryEntry[];
  runEvents: Readonly<Record<string, readonly ContractEvent[]>>;
  /** The run in progress by the newer of the snapshot's and the fold's records (`activeRunOnRecord`), or null. */
  activeRunId: string | null;
  /**
   * THE RUN THE STAGES ARE DRAWN FOR, the newest of the runs they fold (redesign verdict item 6). The card names this
   * run whenever the mission lists it, so the card and the stages never describe two runs; absent or unlisted, the run
   * in progress, else the mission's newest run.
   */
  runOnScreen?: string | null;
  acknowledgements: readonly AckRecord[];
  capability: RobotCapability | null;
  /** Each drawn run's own events, by run id: the records the Scene folds. The placement is read from these only. */
  drawnRunEvents: Readonly<Record<string, readonly ContractEvent[]>>;
  /**
   * WHERE THE MISSION SITS (`model/currency.ts`, spec section 12): absent or current, the card is drawn as before; not
   * current, the mission is History's and the card is one neutral dated line with the runtime's current step list.
   */
  currency?: Currency | null;
  /**
   * The day times print without a date: this display's day when the runtime session dates itself, so a held mission from
   * yesterday prints its dates. Absent: the mission's own start day, as before.
   */
  referenceDay?: string | null;
}

export interface OutcomeRun {
  run_id: string;
  /** "Run 2 of 2", or the run id alone for a run the mission does not number. */
  name: string;
  verdict: Verdict | null;
  line: string;
  /** The stage and controller state the run is in or ended in, with the time it entered that state. */
  stage: string | null;
  /** The same stage's id (`find` .. `return`), for the card's `data-run-stage`; null when the run names none. */
  step: PresentationStep | null;
  state: string | null;
  state_at: UtcStamp | null;
}

export interface OutcomeWhere {
  holding: boolean | null;
  holding_words: string;
  gripper: string | null;
  gripper_at: UtcStamp | null;
  base: string;
  arm: string;
}

export interface OutcomeCardModel {
  /** `past`: the mission on the record is History's (spec section 12); the card is one neutral line and the steps. */
  kind: 'none' | 'carried' | 'ended' | 'past';
  /**
   * The last mission read's refusal code, or null when it answered or has not answered yet. With no mission it decides
   * the card's one line: `no_mission_recorded` is the runtime saying there is none; any other code says the read failed.
   */
  mission_error: string | null;
  mission_id: string | null;
  verdict: Verdict | null;
  started: UtcStamp | null;
  ended: (UtcStamp & { label: string }) | null;
  duration: string | null;
  run: OutcomeRun | null;
  /** Where the card's run placed the block, in the Scene's own line (spec 4.4); null when its records carry no placement. */
  placement: string | null;
  /** The sentence the card leads with while the mission waits, or null. */
  held: string | null;
  /**
   * THE RUNTIME'S OWN DETAIL OF A STOPPED MISSION, in its voice ("The runtime's detail: stopped while carrying a target"),
   * or null (verify-r4 must_fix 1). It is what says how the mission came to end STOPPED, a stop request on the record or
   * none; the card never supplies a cause of its own for it.
   */
  mission_detail: string | null;
  cause: { detail: string | null; reason_code: string; lines: string[]; limit: string | null; frame_id: string | null } | null;
  /**
   * WHAT THE ROBOT DID AND WHAT THE MISSION CHECK REFUSED (spec 3.2.1; `model/missionCheck.ts`), for the card's run when
   * its attempt ended on a mission binding or placement refusal; null otherwise. Said beside the cause, never in its place.
   */
  check: MissionCheck | null;
  where: OutcomeWhere | null;
  steps: NextStep[];
  /** The step that is this card's primary control, when the rule of 4.3 names one. */
  primary_step: string | null;
  /** The key an acknowledgement of this ending carries, or null when nothing here is acknowledged. */
  ack_key: string | null;
  acknowledged: AckRecord | null;
  /** "<Mission word>: <mission id> | shinro" while an ending is unacknowledged, else null. */
  document_title: string | null;
  /** The sentence of the collapsed card. */
  collapsed_line: string | null;
  /** "Last mission: failed, ended 2026-09-27 14:24:40Z (in History)" for a mission that is History's, else null. */
  past_line: string | null;
  /** What the card says first for such a mission: none in this runtime session, or none today (same session, a new day). */
  past_none: string | null;
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function lastAttempt(mission: MissionRecord): { target: MissionTargetRecord | null; attempt: MissionAttemptRecord | null } {
  const target = mission.targets.find((item) => item.target_id === mission.selected_target) ?? mission.targets[mission.targets.length - 1] ?? null;
  const attempts = target?.attempts ?? [];
  return { target, attempt: attempts[attempts.length - 1] ?? null };
}

/**
 * The key of the ending on the record: the mission, when it started, its newest attempt and run, and its word. Null while
 * not ended. THE START IS PART OF IT (redesign verdict item 13): the in-page rehearsal numbers its mission and runs afresh
 * on every load, so without it a second rehearsal ended with the key of the first and was born acknowledged.
 */
export function ackKeyOf(mission: MissionRecord | null): string | null {
  if (mission === null || !missionEnded(mission.state)) return null;
  const { attempt } = lastAttempt(mission);
  const runs = mission.targets.flatMap((item) => item.runs);
  const run = attempt?.run_id ?? runs[runs.length - 1] ?? 'no-run';
  return `${mission.mission_id}:${mission.started_wall_utc ?? 'no-start'}:${String(attempt?.attempt ?? 0)}:${run}:${mission.state}`;
}

/**
 * Whether an acknowledgement can be of this mission's ending: never when it was made before the mission started, which
 * an acknowledgement of an ending cannot have been, and never when its own time does not read. A mission that records
 * no start is compared by its key alone.
 */
function acknowledgedAfterStart(ack: AckRecord, mission: MissionRecord): boolean {
  const at = parseUtcMs(ack.acknowledged_at);
  if (at === null) return false;
  const started = parseUtcMs(mission.started_wall_utc);
  return started === null || at >= started;
}

function payloadOf(event: ContractEvent): Record<string, unknown> {
  return ((event as { payload?: unknown }).payload ?? {}) as Record<string, unknown>;
}

function runOf(input: OutcomeInput, mission: MissionRecord, runId: string, day: string | null): OutcomeRun {
  const events = input.runEvents[runId] ?? [];
  const ordinal = runOrdinal(mission, runId);
  const name = ordinal === null ? `Run ${runId}` : `Run ${String(ordinal.n)} of ${String(ordinal.of)}`;
  const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
  // THE RUN'S WORD, from its own events only: none until they are read (V13), never guessed from the finished-run list.
  const verdict = runVerdictOf(events);
  const states: string[] = [];
  let state: string | null = null;
  let stateAt: string | null = null;
  for (const event of events) {
    if (event.kind !== 'state.transition') continue;
    states.push(event.payload.to_state);
    state = event.payload.to_state;
    stateAt = event.t_wall_utc;
  }
  const phase = finished === undefined ? state : (typeof payloadOf(finished)['phase'] === 'string' ? (payloadOf(finished)['phase'] as string) : state);
  if (phase !== state) stateAt = null;
  const stepOf = phase === null ? null : presentationStepInContext(phase, states.slice(0, -1));
  const step = stepOf === null || stepOf === 'terminal' || stepOf === 'unknown' ? null : stepOf;
  const stage = step === null ? null : PRESENTATION_STEP_LABEL[step];
  const running = runId === input.activeRunId && finished === undefined;
  const line = `${name}${verdict === null ? (running ? ' running' : '') : ` ${lowerFirst(verdict.word)}`}, ${runId}`;
  return { run_id: runId, name, verdict, line, stage, step, state: phase, state_at: utcStamp(stateAt, day) };
}

function holdingWords(holding: boolean | null): string {
  if (holding === true) return 'Holding the block';
  if (holding === false) return 'Gripper empty';
  return 'Not known whether the gripper holds the block; a cube may still be held';
}

function whereOf(attempt: MissionAttemptRecord | null, stop: MissionRecord['hold'] | null, events: readonly ContractEvent[], day: string | null): OutcomeWhere | null {
  const state = stop?.stop_state ?? attempt?.stop_state ?? null;
  if (state === null) return null;
  const measured = [...events].reverse().find((event) => event.kind === 'measured.state');
  const joints = measured === undefined ? null : payloadOf(measured)['joints'];
  const gripperValue = typeof joints === 'object' && joints !== null ? (joints as Record<string, unknown>)['gripper'] : null;
  const gripper = recordedValue(gripperValue);
  return {
    holding: state.holding,
    holding_words: holdingWords(state.holding),
    gripper: gripper === null ? null : `gripper ${gripper}`,
    gripper_at: measured === undefined ? null : utcStamp(measured.t_wall_utc, day),
    // WHAT THE FIELDS RECORD, not a position: the runtime sets base_ahead when a BASE_ADVANCE has no base_returned
    // measured true after it, and moved on any action.started, the base's included (mission_execution.py).
    base:
      state.base_ahead === true
        ? 'The base advanced and was not measured back'
        : state.base_ahead === false
          ? 'The base did not advance, or was measured back'
          : 'Not known whether the base advanced',
    arm: state.moved === true ? 'The robot moved (an action started)' : state.moved === false ? 'No action started' : 'Not known whether an action started',
  };
}

export function outcomeCard(input: OutcomeInput): OutcomeCardModel {
  const mission = input.mission;
  const none: OutcomeCardModel = {
    kind: 'none', mission_error: input.missionError, mission_id: null, verdict: null, started: null, ended: null, duration: null, run: null, placement: null, held: null, mission_detail: null, cause: null, check: null, where: null,
    steps: [], primary_step: null, ack_key: null, acknowledged: null, document_title: null, collapsed_line: null, past_line: null, past_none: null,
  };
  if (mission === null) return none;
  const day = input.referenceDay === undefined ? utcDayOf(mission.started_wall_utc) : input.referenceDay;
  const verdict = missionVerdict({ state: mission.state, waiting_reason: mission.waiting_reason, stop_requested: mission.stop_requested ?? false });
  const currency = input.currency ?? null;
  if (currency !== null && !currency.current && missionEnded(mission.state)) {
    // HISTORY'S MISSION (spec section 12): no verdict colour, no cause, no "where the robot was left", no acknowledgement,
    // no document title and no step list. The date is always printed. An ended mission's step list is the runtime's
    // reading of that mission's last stop state as recorded at its end (verify-r1 M2), not a reading of now, so it is
    // History's; the screen keeps Return arm beside this line, guarded by the capability list read now.
    const end = missionEndTime(mission, input.history);
    // WHICH TIME IT IS, in words (verify-r3/r4 S3): the mission's end when the record gives one, else its newest record.
    const endAt = end?.iso ?? null;
    const when = utcStamp(endAt ?? currency.at, null);
    return {
      ...none,
      kind: 'past',
      mission_id: mission.mission_id,
      past_none: currency.reason === 'earlier_day' ? 'No mission yet today.' : 'No mission yet in this runtime session.',
      past_line: `Last mission: ${lowerFirst(verdict.word)}${when === null ? '' : `, ${endAt !== null ? 'ended' : 'last recorded'} ${when.text}`} (in History)`,
    };
  }
  const ended = missionEnded(mission.state);
  const { attempt } = lastAttempt(mission);
  const runs = mission.targets.flatMap((item) => item.runs);
  const onScreen = input.runOnScreen ?? null;
  const runId =
    onScreen !== null && runs.includes(onScreen)
      ? onScreen
      : input.activeRunId !== null && runs.includes(input.activeRunId)
        ? input.activeRunId
        : (runs[runs.length - 1] ?? null);
  const run = runId === null ? null : runOf(input, mission, runId, day);
  // NOT "MISSION RUNNING" BESIDE A RUN THAT ENDED (verify-r1 must_fix 6): the mission record says RUNNING, the card's run
  // has ended by its own records, and no other run is in progress. The record has not said yet what the mission did at
  // that end; the card says so in this display's voice until it does (the store reads it again at the run's end).
  const betweenRuns =
    mission.state === 'RUNNING' && mission.stop_requested !== true && run !== null && run.verdict !== null && (input.activeRunId === null || input.activeRunId === run.run_id);
  const headline = betweenRuns ? RUN_ENDED_READING : verdict;
  const events = runId === null ? [] : (input.runEvents[runId] ?? []);
  const end = missionEndTime(mission, input.history);
  const endStamp = end === null ? null : utcStamp(end.iso, day);
  const hold = mission.state === 'WAITING_FOR_CAPABILITY' ? (mission.hold ?? null) : null;
  // THE PLACEMENT: the card's run's own records as the Scene reads them, folded by the Scene's fold and said in the
  // Scene's line. Nothing when the run is not drawn or its records carry no PLACED line.
  const placed = runId === null ? null : foldSceneMap(input.drawnRunEvents[runId] ?? []).placed;

  // WHY IT STOPPED, for a run that ended with a reason: the stop's own records, never another action's.
  const causeOf = stopCause(events, day);
  const cause =
    causeOf === null || CAPABILITY_HOLD_CODES.has(causeOf.reason_code ?? '')
      ? null
      : {
          detail: causeOf.detail,
          reason_code: causeOf.reason_code,
          lines: causeOf.lines,
          limit: PLACEMENT_LIMIT_CODES.has(causeOf.reason_code) ? placementLimit(attempt?.measurements ?? []) : null,
          frame_id: causeOf.frame_id,
        };

  // THE MISSION CHECK, for the card's run only: its own attempt when the mission recorded one, and its own events.
  const runAttempt = runId === null ? null : ([...mission.targets.flatMap((item) => item.attempts ?? [])].reverse().find((item) => item.run_id === runId) ?? null);
  const check = missionCheck(runAttempt, events);

  let held: string | null = null;
  if (mission.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === 'capability_unavailable') {
    const capability = input.capability?.name ?? 'the base capability';
    const measured = mission.targets.find((item) => item.target_id === mission.selected_target)?.measured_mode ?? null;
    held = `Waiting for Cake: ${run === null ? 'the run' : lowerFirst(run.name)} measured ${measured ?? 'a placement'} and needs ${capability}.`;
  } else if (hold !== null) {
    held = 'Held for you: the runtime waits for your decision after the failed attempt.';
  }

  const steps = missionSteps(mission).map(nextStep);
  // THE PRIMARY STEP: the first entry, when it is a control on this card and no by-hand step comes before it (rule 4.3).
  const first = steps[0];
  const primary = first !== undefined && first.control !== null && !(first.token === 'move_block_then_retry') ? first.token : null;

  const key = ackKeyOf(mission);
  const acknowledged = key === null ? null : (input.acknowledgements.find((item) => item.key === key && acknowledgedAfterStart(item, mission)) ?? null);
  const ackStamp = acknowledged === null ? null : utcStamp(acknowledged.acknowledged_at, day);
  const collapsed =
    acknowledged === null
      ? null
      : `Mission ${lowerFirst(verdict.word)}${endStamp === null ? '' : ` at ${endStamp.text}`}. Acknowledged on this display at ${ackStamp?.text ?? acknowledged.acknowledged_at}.`;

  return {
    kind: ended ? 'ended' : 'carried',
    mission_error: input.missionError,
    mission_id: mission.mission_id,
    verdict: headline,
    started: utcStamp(mission.started_wall_utc, day),
    ended: end === null || endStamp === null ? null : { ...endStamp, label: end.title },
    duration: end === null ? null : durationWords(mission.started_wall_utc, end.iso),
    run,
    placement: placed === null ? null : placementLine(placed),
    held,
    mission_detail: mission.state === 'STOPPED' && mission.detail !== null && mission.detail !== '' ? `The runtime's detail: ${mission.detail}` : null,
    cause,
    check,
    where: ended || hold !== null ? whereOf(attempt, hold, events, day) : null,
    steps,
    primary_step: primary,
    ack_key: key,
    acknowledged,
    document_title: ended && acknowledged === null ? `${verdict.word}: ${mission.mission_id} | shinro` : null,
    collapsed_line: collapsed,
    past_line: null,
    past_none: null,
  };
}
