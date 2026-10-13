// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FAILURE BANNER: the stage that failed, the measured sentence, the frame, the rule that decided, and what can be
// done next, across the whole width of the screen and out of any drawer.
//
// On 2026-09-26 the failure sentence and the decision frame were in drawers, and debugging needed a terminal. This
// puts the same facts in one band. It invents none of them: the sentence is `explainFailure`'s (the stage gloss filled
// with the raise site's own values, else the controller's own words), the numbers and the rule are the stage
// timeline's row for the failed stage, the frame is the one the failure named.
//
// THE THREE NEXT ACTIONS ARE ALWAYS SHOWN, each on or off with its reason on the screen:
//
//   TRY AGAIN is the mission's retry, which exists only while the mission is held for the operator's decision
//   (`waiting_reason` operator_decision) and only on a runtime that offers it. It is a motion door, so the screen also
//   passes in the refusal Start's guards make (`retryGuard`, from `panels/Actions.retryRefusal`), and the button is off
//   with that reason. Anything else says why it is off: this console never starts a fresh attempt by any other door.
//   RETURN ARM is the runtime's `return_arm` control with its own guards; the banner says why it is the next step for
//   this reason code, or that the runtime does not report it. Whether it is on is read from the LIVE capability list,
//   the one the embedded control reads: the fold of a finished run read back from history starts from an empty list,
//   and a statement about the robot now is not taken from it.
//   STOP is the header's Stop, repeated here because a failure is where somebody reaches for it.

import { explainFailure, nextActionsFor, stopRecordLines } from './failure';
import type { FailureExplanation } from './failure';
import type { CommandScope } from './command';
import type { MissionRecord } from './missionRecord';
import { holdBlock, holdOffer, missionOfferBlock, OPERATOR_DECISION, targetOfRun } from './missionView';
import { PRESENTATION_STEP_LABEL, presentationStepInContext } from './phase';
import { placementLimit } from './placementLimit';
import { runInProgressByRecord } from './selectedRun';
import type { PresentationStep } from './phase';
import { MISSION_HOLD_CODES, stageRuleWords } from './stageTimeline';
import type { StageNumber, StageTimeline } from './stageTimeline';
import type { ViewState } from './types';
import { outcomeIsOfRunOnScreen, runIsActive } from './steps';

export { OPERATOR_DECISION };

export interface BannerAction {
  id: 'retry' | 'return_arm' | 'stop';
  label: string;
  enabled: boolean;
  /** Why it is off, or, when on, why it is the next step. */
  reason: string | null;
}

export interface FailureBannerModel {
  run_id: string | null;
  outcome: string;
  stage: PresentationStep | null;
  /** "Pick", or the controller's state when it belongs to no stage. */
  stage_label: string;
  /** The controller state it stopped in, as recorded. */
  state: string | null;
  headline: string;
  /** The measured sentence: the gloss with the record's values, the wordy gloss, or the controller's own words. */
  sentence: string;
  code: string | null;
  /** The controller's own sentence, verbatim, when it differs from `sentence`. */
  detail: string | null;
  frame_id: string | null;
  rule_words: string;
  numbers: StageNumber[];
  /** True while the mission is held for the operator's decision after this failure. */
  held_for_decision: boolean;
  /**
   * For a placement refusal: the limit that decided it, its shortfall and the hint, as the record carries them
   * (`placementLimit`); the words 'limit not recorded' when the record carries counts only. Null for every other failure.
   */
  limit: string | null;
  actions: BannerAction[];
  explanation: FailureExplanation;
}

export interface BannerInput {
  /** The selected run's view: the facts of the failure are read from it. */
  view: ViewState;
  /** The live snapshot's view: every on or off of a control is read from it, never from a run's fold. */
  live: ViewState;
  scope: CommandScope;
  mission: MissionRecord | null;
  timeline: StageTimeline;
  /** True when the source this page talks to offers the mission retry at all. */
  retryOffered: boolean;
  /** Why nothing on this page may command, or null. */
  readOnlyReason: string | null;
  /** True while a stop request from this page is in flight. */
  stopBusy: boolean;
  /**
   * Why Try again may not be sent now by the motion guards Start and every control share (`retryRefusal`), or null.
   * The banner's own reasons (no retry door, no mission, no hold) are said first.
   */
  retryGuard: string | null;
  /** Why the Return arm control drawn in the banner is off, from the guards that control makes (`offReason`, `robotBlock`), or null. */
  returnGuard: string | null;
}

const HEADLINE: Record<string, string> = {
  failed: 'failed',
  refused: 'refused',
  stopped: 'stopped',
  unknown: 'ended without a stated outcome',
};

function sentenceCase(text: string): string {
  const trimmed = text.trim();
  return trimmed === '' ? trimmed : trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

/**
 * THE REFUSALS THAT A PLACEMENT LIMIT DECIDES: the binding's refusal of the one cube (start 3 and the block of 21:17)
 * and the placement solver's refusals. Their banner leads with the limit, and says when the record carries none.
 */
export const PLACEMENT_LIMIT_CODES: ReadonlySet<string> = new Set([
  'mission_binding_candidate_not_placeable',
  'search_placement_infeasible',
  'placement_infeasible_while_held',
]);

/**
 * THE REFUSALS THAT MAY CARRY A PLACEMENT LIMIT: a declared target's no-candidate refusal names the limit of the one cube
 * left when the binding had one to explain. Its limit is stated when the record carries it and nothing is said when it
 * does not: most such refusals are about no cube at all.
 */
export const PLACEMENT_LIMIT_WHEN_RECORDED: ReadonlySet<string> = new Set(['mission_binding_no_candidate']);

/**
 * True while the mission record says it is held for the operator's decision: the WAITING state with that reason. The
 * runtime keeps the waiting reason on the record after a Stop ends the hold (it serves STOPPED with no hold), so the
 * reason alone does not say the mission is still held.
 */
export function heldForDecision(mission: MissionRecord | null): boolean {
  return mission !== null && mission.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === OPERATOR_DECISION;
}

/**
 * The words for Try again after a Stop ended the mission, or null when it did not. The runtime keeps the waiting reason
 * on a STOPPED record, so a mission a Stop ended is said as stopped, never as held or as still stopping.
 */
export function stoppedMissionReason(mission: MissionRecord | null): string | null {
  if (mission === null || mission.state !== 'STOPPED') return null;
  return `mission ${mission.mission_id} was stopped, so there is no attempt to retry; Start begins a new mission`;
}

/**
 * WHETHER THE STREAM HAS STARTED A RUN THIS FOLD HAS NOT SEEN END: a `run.started` folded here with no end of that run, or
 * of a later one, in the fold (`outcomeIsOfFoldedRun`, read the other way). The stream arrives as the runtime writes it
 * and the snapshot only every 5 s, so this is the newer record of a run in progress, the one another display started
 * included (verdict 2026-09-27 must_fix 2). It is the same rule the mission card and the Stages use to name the run in
 * progress (`runInProgressByRecord`), read from that one place so Stop and the card cannot disagree about it.
 */
export function streamRunOpen(live: ViewState): boolean {
  return runInProgressByRecord(live) !== null;
}

/**
 * WHETHER SOMETHING MAY BE MOVING on evidence newer than the snapshot's capability list: a command from this tab that is
 * not settled yet (`pending`: sent, and no snapshot read after its answer has answered; the runtime publishes a run
 * before it answers the command that started it), the runtime's or the fold's run in progress (`runIsActive`), or a run
 * the stream started and has not ended (`streamRunOpen`).
 */
export function runMayBeActive(live: ViewState, pending = false): boolean {
  return pending || runIsActive(live) || streamRunOpen(live);
}

/**
 * WHETHER THE RECORD SHOWS A RUN THE SNAPSHOT'S LIST MAY NOT SHOW YET: the fold's own run in progress (a controller state
 * entered and no end), or a run the stream started and has not ended. The snapshot's own `active_run` is left out on
 * purpose: the capability list came in the same answer and already says what that run allows.
 */
export function recordShowsRunBeyondList(live: ViewState): boolean {
  return streamRunOpen(live) || runIsActive({ ...live, active_run: null });
}

/** Whether the mission record shows a mission the runtime is carrying: running, or waiting, and not ended. */
function missionCarried(mission: MissionRecord | null): boolean {
  return mission !== null && (mission.state === 'RUNNING' || mission.state === 'WAITING_FOR_CAPABILITY');
}

/**
 * WHY STOP IS OFF, or null, for the header's Stop and the banner's: a stop already in flight, a runtime this console cannot
 * read, a hold whose list does not offer it, or the runtime's own `stop` capability off with its reason (read_models:
 * enabled while a run is active or a mission is carried, "there is no run to stop" otherwise). A runtime whose list
 * carries no `stop` has said nothing about it, and Stop stays on: a stop with nothing to stop is refused and says so.
 *
 * STOP IS ON FROM THE MOMENT A COMMAND IS SENT UNTIL THE RECORD SAYS NOTHING RUNS (verdict 2026-09-27 must_fix 2). The list
 * is a snapshot up to one poll old, and a hold is a record from before the command. While a command from this tab is not
 * settled (`pending`) or the record shows a run (`runMayBeActive`), neither turns Stop off; and a mission the mission
 * record shows carried is what the runtime serves Stop for with no run, so a list's "no run" from before it does not
 * turn Stop off either. A stop that finds nothing to stop is answered `no_active_run` and moves nothing.
 */
export function stopOffReason(live: ViewState, stopBusy: boolean, mission: MissionRecord | null, pending = false): string | null {
  if (stopBusy) return 'a stop request is in flight';
  if (!live.contract_compatible) return 'the runtime speaks a contract version this console does not read';
  if (runMayBeActive(live, pending)) return null;
  const hold = heldForDecision(mission) ? (mission?.hold ?? null) : null;
  if (hold !== null && !hold.next_actions.includes('stop')) return `the runtime's hold offers ${hold.next_actions.join(', ') || 'nothing'}, not Stop`;
  const capability = live.capabilities.find((item) => item.id === 'stop') ?? null;
  if (capability !== null && capability.state !== 'enabled') {
    if (capability.reason_code === 'no_active_run' && missionCarried(mission)) return null;
    return capability.reason ?? capability.reason_code ?? 'the runtime turned it off and sent no reason';
  }
  return null;
}

/** The words for a retry the runtime already wrote for the held attempt, which it parks before the fresh attempt. */
export const RETRY_ALREADY_ASKED = 'a retry of this attempt was already asked for; the runtime parks the arm and then starts the fresh attempt';

/** The line drawn when the failure names no frame: for the stage, else the controller state, else the run. */
export function noFrameSentence(model: Pick<FailureBannerModel, 'stage' | 'stage_label' | 'state'>): string {
  const what = model.stage !== null ? `the ${model.stage_label} stage` : model.state !== null ? `the ${model.state} state` : 'this run';
  return `No frame was recorded for this failure: the controller named none for ${what}.`;
}

export function failureBanner(input: BannerInput): FailureBannerModel | null {
  const explanation = explainFailure(input.view, input.scope);
  if (explanation === null) return null;
  // A LATER RUN IS IN PROGRESS: the outcome is a finished earlier run's end, which is not this screen's failure.
  if (!outcomeIsOfRunOnScreen(input.view)) return null;
  // A run that ended because the mission holds for the capability it measured the target needs is not a failure: the
  // held-mission panel says so and the Carry stage carries the hold. While the mission is still RUNNING the hold is on
  // its way (the run ends before the mission writes its wait), and that is the same hand-over. It is explained as a
  // failure only when the mission record shows no mission carried: one that ended with it, or none at all.
  const carried = input.mission?.state === 'WAITING_FOR_CAPABILITY' || input.mission?.state === 'RUNNING';
  if (carried && MISSION_HOLD_CODES.has(explanation.reason_code ?? '')) return null;

  const state = explanation.phase;
  const assigned = state === null ? null : presentationStepInContext(state, (input.view.machine?.history ?? []).map((item) => item.state));
  const stage = assigned === null || assigned === 'terminal' || assigned === 'unknown' ? null : assigned;
  const stageLabel = stage === null ? (state ?? 'the run') : PRESENTATION_STEP_LABEL[stage];
  const row = stage === null ? null : input.timeline.rows.find((item) => item.step === stage) ?? null;

  const sentence =
    explanation.sentence ??
    explanation.gloss ??
    explanation.detail ??
    (explanation.stop_request !== null
      ? (stopRecordLines(explanation.stop_request)[0] ?? 'The run was stopped on request.')
      : explanation.unexplained
        ? 'The record does not say why.'
        : `The controller recorded ${explanation.reason_code ?? 'no reason code'}.`);

  const held = heldForDecision(input.mission);
  // WHAT THE HOLD OFFERS IS THE RUNTIME'S RECORD OF IT (`hold.next_actions`), never this console's reading of the code.
  const offer = holdOffer(input.mission);
  const retryReason = input.readOnlyReason
    ?? (!input.retryOffered
      ? 'this runtime does not offer a mission retry'
      : input.mission === null
        ? 'no mission is carried on this runtime; Start begins a new one'
        : stoppedMissionReason(input.mission) ??
          (!held
          ? 'the mission is not held for a decision, so there is no attempt to retry'
          : offer === 'stop_only'
            ? `the runtime offers only Stop for this hold (its next actions: ${(input.mission.hold?.next_actions ?? []).join(', ') || 'none'})`
            : offer === 'not_served' || offer === null
              ? 'the runtime served no record of the held attempt, so no retry is offered'
              : input.mission.hold?.retry_requested === true
                ? RETRY_ALREADY_ASKED
                : input.retryGuard));
  const retryWhy =
    offer === 'move_block_then_retry'
      ? 'move the block by hand first; Try again then parks the arm with the proven exit and starts a fresh attempt'
      : 'park the arm with the proven exit, then start a fresh attempt';

  // The reason code's next step, answered by the LIVE list: the run's own fold says why, the runtime now says whether.
  const returnArm = nextActionsFor(explanation.reason_code, input.live.capabilities).find((item) => item.capability_id === 'return_arm') ?? null;
  const capability = input.live.capabilities.find((item) => item.id === 'return_arm') ?? null;
  // WHILE THE RUNTIME SERVES A HOLD, ITS LIST IS WHAT A PRESS CAN DO: Return arm is offered only when the hold lists it.
  // The embedded control reads the same function (`panels/Actions`), so the button and this line never disagree.
  // A RUN THE MISSION CARRIED is answered by the mission's own list too: after its end the runtime lists Return arm only
  // when the arm may have moved. A run of no mission is answered by the capability list alone.
  const ofMission = input.mission !== null && explanation.run_id !== null && targetOfRun(input.mission, explanation.run_id) !== null;
  const holdBlocksReturn = holdBlock(input.mission, 'return_arm') ?? (ofMission ? missionOfferBlock(input.mission, 'return_arm') : null);
  const returnReason =
    input.readOnlyReason ??
    holdBlocksReturn ??
    input.returnGuard ??
    (returnArm !== null
      ? returnArm.enabled
        ? returnArm.why
        : returnArm.reason
      : capability === null
        ? 'this runtime does not report a control that returns the arm'
        : capability.state === 'enabled'
          ? 'the arm goes back to the saved low scan pose'
          : (capability.reason ?? capability.reason_code ?? 'the runtime turned it off and sent no reason'));
  const returnEnabled =
    input.readOnlyReason === null && holdBlocksReturn === null && input.returnGuard === null && (returnArm?.enabled ?? capability?.state === 'enabled');

  return {
    run_id: explanation.run_id,
    outcome: explanation.outcome,
    stage,
    stage_label: stageLabel,
    state,
    headline: `${stageLabel} ${HEADLINE[explanation.outcome] ?? explanation.outcome}`,
    sentence: sentenceCase(sentence),
    code: explanation.reason_code,
    detail: explanation.detail !== null && explanation.detail !== sentence ? explanation.detail : null,
    // THE FRAME THE FAILING STAGE DECIDED ON: the failure's own frame, else the stage's own failing predicate's. Never a
    // frame another stage or a passing predicate consumed; null when none was recorded, and the banner says so.
    frame_id: row?.failed_frame_id ?? input.view.outcome.failure?.observation_ids[0] ?? null,
    rule_words: row === null ? 'rule not recorded' : stageRuleWords(row),
    numbers: row?.numbers ?? [],
    held_for_decision: held,
    limit: PLACEMENT_LIMIT_CODES.has(explanation.reason_code ?? '')
      ? (placementLimit(input.view.outcome.failure?.measurements ?? []) ?? 'limit not recorded: the record carries no binding limit for this refusal')
      : PLACEMENT_LIMIT_WHEN_RECORDED.has(explanation.reason_code ?? '')
        ? placementLimit(input.view.outcome.failure?.measurements ?? [])
        : null,
    actions: [
      { id: 'retry', label: 'Try again', enabled: retryReason === null, reason: retryReason ?? retryWhy },
      { id: 'return_arm', label: 'Return arm', enabled: returnEnabled, reason: returnReason },
      {
        id: 'stop',
        label: 'Stop',
        enabled: stopOffReason(input.live, input.stopBusy, input.mission) === null,
        reason: stopOffReason(input.live, input.stopBusy, input.mission) ?? 'ends the program in software; it is not an emergency stop',
      },
    ],
    explanation,
  };
}
