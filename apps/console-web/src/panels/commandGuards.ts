// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The checks that sit between a button and a motion command, each of which can only ever turn a control OFF.
//
// Nothing here can enable anything. The server's capability list is what says a control may be offered; these are the
// five conditions under which this console refuses although the list says enabled, and each of them exists because
// the list is a snapshot that can be a few seconds old:
//
//   AUTHORITY   the runtime says a host command lease is REQUIRED for this mode and it does not hold one, or the host
//               has not answered for longer than this console counts as current. A lease nobody is renewing is not
//               authority, and motion commanded under one lands in a robot somebody else may be holding.
//   ROBOT       the list says enabled on the strength of an answer about the ROBOT COMPUTER that reached this browser
//               too long ago to stand for the robot now. The runtime ages its own reading out and then reports no
//               answer at all, and that arrives here as a control the list already turned off, which needs nothing
//               from this file. What this file adds is the one case the runtime cannot see: a snapshot that stopped
//               arriving and left the last enabled list on the screen. An answer nobody has refreshed is never drawn
//               as a ready robot.
//   HOLDING     a command whose behaviour depends on whether the cube is held cannot be sent with a guess. The value
//               is derived from the record when the record settles it, and left UNKNOWN when it does not. An unknown
//               holding blocks the command; it never becomes false by default.
//   PREVIEW     Execute references a plan that was previewed. The preview is bound to the runtime, the owner, the
//               controller state and the exact parameters, and any change to those invalidates it.
//   IN FLIGHT   a second start while the first one's fate is not settled is a second run. It is refused here, in the
//               browser, before the request is built.
//   RUN         a command from this tab that is not settled, or a run the record shows before the list does. The
//               runtime publishes a run before it answers the command that started it and accepts only Stop while one
//               is active; the list says so only from the next snapshot (verdict 2026-09-27 must_fix 2).
//   ANSWERING   the runtime is not answering, or its snapshot, or a read a control is offered from, is not current
//               (verdict 2026-09-27 must_fix 3; spec 4.1 and 4.3, the Stale and Disconnected columns). The list on the
//               screen is then a statement from before, and a press would go to a runtime that is not answering.
//               Stop is the one control none of this turns off (`model/failureBanner.stopOffReason`).
//
// Preview health is deliberately NOT one of these. A camera slot that is quiet, or a pad frame that a phase lost, is
// not authority over the robot, and turning motion off for one would refuse commands whose predicates never needed it.

import type { Capability, Command } from '@kiwi/demo-contracts';
import { millis, textOr } from '../model/format';
import { reasonCodeLabel } from '../model/reason';
import { ROBOT_ANSWER_STALE_AFTER_MS, robotReading } from '../model/robot';
import { recordShowsRunBeyondList } from '../model/failureBanner';
import { activeRunOnRecord } from '../model/selectedRun';
import { missionEnded } from '../model/verdict';
import { motionPermissionView, robotLink } from '../model/robotStatus';
import { utcDayOf, utcStamp } from '../model/utcTime';
import { currencyContext, missionCurrency, sessionWords } from '../model/currency';
import type { MissionRecord } from '../model/missionRecord';
import type { Readiness as CycleReadiness } from '../model/steps';
import type { PredicateView, ViewState } from '../model/types';
import type { CommandTrack, StoreSnapshot } from '../store/store';

/** The commands whose contract arguments carry `holding`. It is explicit on each of them and has no default. */
export const HOLDING_COMMANDS: readonly Command['type'][] = ['observe', 'manual_pan', 'manual_move', 'manual_base_pulse'];

/**
 * How long the host may go without answering before this console stops counting a held lease as current authority.
 * It is a display and refusal threshold of this console's own, and it is stated on the screen beside what it refuses.
 */
export const AUTHORITY_STALE_AFTER_MS = 4000;

/** How long a preview stands before it is taken as too old to execute against. Stated on the control. */
export const PREVIEW_STALE_AFTER_MS = 120_000;

/**
 * Whether the cube is held, as the RECORD settles it, with the record it rests on.
 *
 * The runtime's own definition (`demo_runtime/mission_execution.py`, `stop_state_from_events`): the jaws closed on
 * something (`closed_on_something` true) or `object_secured` true, until a RELEASED, or a GRASP_SET_DOWN whose jaws
 * then measured open (`gripper_opened` true); a GRASP_CLOSE with no verdict after it is unknown. The mission's newest
 * attempt is what the runtime computed for its run (`stop_state.holding`) and is read first; this tab's fold is read
 * instead only when it holds a run that began after that attempt was written, or when there is no attempt. Where neither
 * settles it the value is null, and null is carried to the screen as not known rather than resolved to false.
 */
export interface HoldingReading {
  value: boolean | null;
  /** The record the reading rests on, in the same form the reason block uses. Null when nothing settles it. */
  basis: string | null;
}

/** The controller states in which the gripper has let go. A transition to one of them ends a holding verdict. */
const RELEASED_STATES: ReadonlySet<string> = new Set(['RELEASED', 'PLACED', 'DONE_ALREADY_PLACED']);

function lastPredicate(view: ViewState, name: string): PredicateView | null {
  let found: PredicateView | null = null;
  for (const predicate of view.predicates) {
    if (predicate.predicate === name && (found === null || predicate.seq >= found.seq)) found = predicate;
  }
  return found;
}

interface HoldingMark {
  seq: number;
  kind: 'close' | 'released' | 'set_down' | 'closed_on' | 'secured' | 'opened';
  value: boolean;
  words: string;
}

/**
 * The runtime's holding over what this fold keeps, in record order: the controller's state entries and the newest
 * verdict of each grasp predicate. `known` is false when the fold holds none of those records.
 */
function holdingFromFold(view: ViewState): { known: boolean; reading: HoldingReading } {
  const marks: HoldingMark[] = [];
  const machine = view.machine;
  if (machine !== null) {
    const entries = [...machine.history, { state: machine.state, seq: machine.since_seq }];
    const seen = new Set<number>();
    for (const entry of entries) {
      if (seen.has(entry.seq)) continue;
      seen.add(entry.seq);
      const words = `state.transition to ${entry.state} at seq ${String(entry.seq)}`;
      if (entry.state === 'GRASP_CLOSE') marks.push({ seq: entry.seq, kind: 'close', value: true, words });
      else if (RELEASED_STATES.has(entry.state)) marks.push({ seq: entry.seq, kind: 'released', value: true, words });
      else if (entry.state === 'GRASP_SET_DOWN') marks.push({ seq: entry.seq, kind: 'set_down', value: true, words });
    }
  }
  const closed = lastPredicate(view, 'closed_on_something');
  if (closed !== null && typeof closed.value === 'boolean') {
    marks.push({ seq: closed.seq, kind: 'closed_on', value: closed.value, words: `predicate.evaluated closed_on_something ${String(closed.value)} at seq ${String(closed.seq)}` });
  }
  const secured = lastPredicate(view, 'object_secured');
  if (secured !== null && secured.value === true) {
    marks.push({ seq: secured.seq, kind: 'secured', value: true, words: `predicate.evaluated object_secured true at seq ${String(secured.seq)}` });
  }
  const opened = lastPredicate(view, 'gripper_opened');
  if (opened !== null && opened.value === true) {
    marks.push({ seq: opened.seq, kind: 'opened', value: true, words: `predicate.evaluated gripper_opened true at seq ${String(opened.seq)}` });
  }
  marks.sort((a, b) => a.seq - b.seq);
  if (!marks.some((mark) => mark.kind !== 'released' && mark.kind !== 'set_down')) {
    // Only a release on record: the jaws let go, which settles an empty gripper; nothing at all settles nothing.
    const released = [...marks].reverse().find((mark) => mark.kind === 'released');
    return released === undefined ? { known: false, reading: { value: null, basis: null } } : { known: true, reading: { value: false, basis: released.words } };
  }
  let holding = false;
  let openClose = false;
  let setDown = false;
  let basis: string | null = null;
  for (const mark of marks) {
    if (mark.kind === 'close') {
      openClose = true;
      setDown = false;
      basis = mark.words;
    } else if (mark.kind === 'released') {
      holding = false;
      openClose = false;
      setDown = false;
      basis = mark.words;
    } else if (mark.kind === 'set_down') {
      setDown = true;
    } else if (mark.kind === 'closed_on') {
      if (!holding || mark.value) basis = mark.words;
      holding = holding || mark.value;
      openClose = false;
    } else if (mark.kind === 'secured') {
      holding = true;
      openClose = false;
      basis = mark.words;
    } else if (setDown) {
      holding = false;
      openClose = false;
      setDown = false;
      basis = mark.words;
    }
  }
  if (openClose && !holding) return { known: true, reading: { value: null, basis: `${basis ?? 'a close'}, with no verdict after it` } };
  return { known: true, reading: { value: holding, basis } };
}

/** The newest attempt of the mission's selected target, as the runtime wrote it after the attempt's run ended. */
function newestAttempt(mission: MissionRecord | null): NonNullable<MissionRecord['targets'][number]['attempts']>[number] | null {
  if (mission === null) return null;
  const target = mission.targets.find((item) => item.target_id === mission.selected_target) ?? mission.targets[mission.targets.length - 1] ?? null;
  const attempts = target?.attempts ?? [];
  return attempts[attempts.length - 1] ?? null;
}

/**
 * Whether this fold holds a run that began after the attempt was written. Only then is the fold the newer record: the
 * attempt is the runtime's verdict over every event of its run, and a fold of that same run keeps only the newest
 * verdict of each predicate. A fold that cannot date its run is never taken as the newer one.
 */
function foldIsNewer(view: ViewState, attemptWallUtc: string | null): boolean {
  const started = view.run_started?.started_wall_utc ?? null;
  if (started === null || attemptWallUtc === null) return false;
  const startedMs = Date.parse(started);
  const attemptMs = Date.parse(attemptWallUtc);
  return Number.isFinite(startedMs) && Number.isFinite(attemptMs) && startedMs > attemptMs;
}

export function deriveHolding(view: ViewState, mission: MissionRecord | null = null): HoldingReading {
  const fold = holdingFromFold(view);
  const attempt = newestAttempt(mission);
  if (attempt === null || attempt.stop_state === null) return fold.reading;
  if (fold.known && foldIsNewer(view, attempt.t_wall_utc)) return fold.reading;
  const holding = attempt.stop_state.holding;
  const named = `the mission record, attempt ${attempt.attempt === null ? '(unnumbered)' : String(attempt.attempt)} of run ${attempt.run_id ?? '(not named)'}`;
  return {
    value: holding,
    basis: holding === null ? `${named}, whose stop state records holding as unknown` : `${named}, whose stop state records holding ${String(holding)}`,
  };
}

/**
 * THE MISSION THAT MAY SETTLE HOLDING NOW (verify-r3 M3; spec section 12). The runtime restarted today serves yesterday's
 * ended mission, and its newest attempt's stop state is what that mission recorded at ITS end, not a reading of now. So
 * the served mission settles holding only while it is current (`missionCurrency`: not ended, read by this page before it
 * ended, or newest record of this runtime session and today); a mission that is History's is not read for `holding`, and
 * the operator is asked, told which record was set aside and when it was written. A held mission is never History's.
 */
export interface HoldingMission {
  /** The mission deriveHolding may read, or null. */
  mission: MissionRecord | null;
  /** The served mission set aside as History's, with the words that say so, or null. */
  past: { mission_id: string; words: string } | null;
}

export function currentHoldingMission(fresh: Pick<StoreSnapshot, 'mission' | 'history' | 'missionsSeenOpen' | 'view'>, nowMs: number): HoldingMission {
  const mission = fresh.mission;
  if (mission === null) return { mission: null, past: null };
  const entries = fresh.history?.ok === true ? fresh.history.entries : [];
  const context = currencyContext(fresh.view.runtime_session, nowMs);
  const currency = missionCurrency(mission, entries, context, fresh.missionsSeenOpen);
  if (currency.current) return { mission, past: null };
  const at = utcStamp(currency.at, null);
  const when = at === null ? '' : `its newest record ${at.text}, `;
  return {
    mission: null,
    past: {
      mission_id: mission.mission_id,
      words: `The mission record the runtime serves, ${mission.mission_id}, is History's (${when}${sessionWords(currency, context)}), so it does not settle whether the gripper is holding the cube now`,
    },
  };
}

/**
 * Why the runtime's own authority over the robot is not good enough to move it, or null when it is.
 *
 * This reads the runtime's lease report and nothing else. When the runtime says no lease is required for this mode,
 * or says nothing about a lease at all, there is nothing here to refuse on and this returns null: inventing an
 * authority requirement the runtime does not state would refuse every command in a simulation.
 */
/**
 * WHAT A CONTROL SAYS IN PLACE OF THE SNAPSHOT'S `run_active` ONCE THE RECORD HOLDS THAT RUN'S END (redesign r4 verify-r2
 * must_fix 2), in this display's voice.
 *
 * The runtime writes a run's end before it lets go of the run (`demo_runtime/supervisor.py`: the run.finished emit, then
 * `_release`), and the snapshot is a poll behind the stream. So for a moment the capability list still says a run is in
 * progress while the stream has delivered its end: printing that sentence as current put "A run is already in progress"
 * beside a card that says the run, or the mission, ended. The control stays off (the runtime has not said it is idle,
 * and a press could still be refused `run_active`); only the words change, to what this display knows. Null in every
 * other case, and whenever another run is in progress by the record.
 */
export const RUN_ENDED_NOT_IDLE = 'the run has ended by its own record; the runtime has not yet reported itself idle';

export function runEndedNotIdle(view: ViewState, capability: Capability | null): string | null {
  if (capability === null || capability.state === 'enabled' || capability.reason_code !== 'run_active') return null;
  if (view.active_run === null || activeRunOnRecord(view) !== null) return null;
  return RUN_ENDED_NOT_IDLE;
}

/**
 * WHAT A CONTROL SAYS IN PLACE OF THE SNAPSHOT'S `mission_running` ONCE THE MISSION RECORD THIS DISPLAY HOLDS HAS ENDED
 * (redesign r4 verify-r3 must_fix 1), in this display's voice: the sibling of `runEndedNotIdle`, for the mission.
 *
 * A stop at a hold ends no run, and the runtime's mission record can say STOPPED before its snapshot lets go of the
 * mission's slot (verify-r3: STOPPED at +119 ms, the snapshot idle at +142 ms), so for a moment the capability list still
 * says "a mission is being carried" beside a card that says the mission stopped. The control stays off (the runtime has
 * not said it is idle, and a press could still be refused `mission_running`); only the words change, to what this display
 * knows. The sentence names the mission its record is about, so it stays true if another display has started a new
 * mission the snapshot is about: that mission has ended by its record, and the runtime has not reported itself idle.
 */
export const MISSION_ENDED_NOT_IDLE = 'has ended by its own record; the runtime has not yet reported itself idle';

export function missionEndedNotIdle(capability: Capability | null, mission: Pick<MissionRecord, 'mission_id' | 'state'> | null): string | null {
  if (capability === null || capability.state === 'enabled' || capability.reason_code !== 'mission_running') return null;
  if (mission === null || !missionEnded(mission.state)) return null;
  return `mission ${mission.mission_id} ${MISSION_ENDED_NOT_IDLE}`;
}

/**
 * THE CLASSIC SCREEN'S CYCLE READINESS (`model/steps.readiness`) with the snapshot's `run_active` and `mission_running`
 * said as this display knows them once the record holds the end (`runEndedNotIdle`, `missionEndedNotIdle`; should_fix
 * honesty S1). Its action card printed the runtime's clause as current, "What stands in the way of a run: a run is
 * already in progress", beside the run's end. Only the blocker's words change, and only when they are that capability's
 * own sentence: a cycle is still not ready.
 */
export function readinessOnRecord(ready: CycleReadiness, view: ViewState, mission: MissionRecord | null): CycleReadiness {
  if (ready.ready || ready.blocker === null) return ready;
  const capability = view.capabilities.find((item) => item.id === 'start_cycle') ?? null;
  if (capability === null || ready.blocker !== capability.reason) return ready;
  const words = runEndedNotIdle(view, capability) ?? missionEndedNotIdle(capability, mission);
  return words === null ? ready : { ...ready, blocker: words };
}

export function authorityBlock(view: ViewState): string | null {
  const lease = view.lease;
  if (lease === null) return null;
  if (!lease.required) return null;
  if (!lease.held) {
    return `this mode requires a host command lease and the runtime holds none: ${textOr(reasonCodeLabel(lease.reason))}`;
  }
  if (lease.expires_in_ms !== null && lease.expires_in_ms <= 0) {
    return 'the host command lease the runtime holds has expired';
  }
  if (lease.last_reply_age_ms !== null && lease.last_reply_age_ms > AUTHORITY_STALE_AFTER_MS) {
    // NO AGE: the snapshot's reply age grows with every poll while the host is silent, and the words must not tick.
    return `the lease is held and the host has not answered within the ${millis(AUTHORITY_STALE_AFTER_MS)} this console counts as current`;
  }
  return null;
}

/**
 * Why the answer about the ROBOT that this screen is holding is not one a motion command may be offered against, or
 * null when there is nothing here to refuse on.
 *
 * This is one narrow case and it is deliberately not more. Every blocker the runtime itself reports arrives on the
 * capability list, turns the control off there, and carries the runtime's own sentence with it; repeating that here
 * would put the same refusal on the screen twice and let the two drift. What is left is what only this side can see:
 * the runtime answered once that a motor host is running, the snapshot poll then stopped arriving, and the enabled
 * list from before is still drawn. `robotReading` calls that answer not current, and a start is refused on it.
 *
 * It refuses NOTHING on a runtime that reads no robot at all, which is every simulation: inventing a readiness
 * requirement where there is no robot would turn off every control in a mode that has nothing to be ready.
 */
export function robotBlock(view: ViewState, nowMs: number): string | null {
  const reading = robotReading(view, nowMs);
  return reading.state === 'answer_not_current' ? reading.consequence : null;
}

/**
 * WHETHER A COMMAND FROM THIS TAB IS NOT SETTLED: a submit, a mission start or a mission retry that has been sent and
 * whose answer, or the snapshot read asked for after that answer, is not in yet (`StoreSnapshot.commandInFlight`). It
 * may already be a run the capability list on the screen does not show.
 */
export function commandPending(fresh: Pick<StoreSnapshot, 'commandInFlight' | 'startingMission' | 'retryingMission'>): boolean {
  return fresh.commandInFlight || fresh.startingMission || fresh.retryingMission;
}

/** Why a control is off while a command from this tab is not settled (`commandPending`). */
export const COMMAND_NOT_SETTLED =
  "a command sent from this tab may have started a run that the runtime's last snapshot does not show yet, and the runtime accepts only Stop while a run is active";

/** Why a control is off while the record shows a run the capability list does not show yet. */
export const RUN_ON_THE_RECORD = 'the record shows a run in progress, and the runtime accepts only Stop while one is';

/**
 * WHY A COMMAND CONTROL IS OFF WHILE A RUN MAY BE GOING THAT THE LIST DOES NOT SHOW YET, or null: a command from this tab
 * that is not settled (`pending`), or a run on the record the list does not show yet (`recordShowsRunBeyondList`: the
 * fold's run in progress, a `run.started` the stream delivered with no end). Asked after the runtime's own list, so its
 * words win where it has them.
 */
export function runInProgressBlock(view: ViewState, pending: boolean): string | null {
  if (pending) return COMMAND_NOT_SETTLED;
  if (recordShowsRunBeyondList(view)) return RUN_ON_THE_RECORD;
  return null;
}

/** The UTC day a fixed time is printed against: the mission's, else this computer's today, as the demo screen prints it. */
function referenceDay(mission: MissionRecord | null, nowMs: number): string {
  return utcDayOf(mission?.started_wall_utc ?? null) ?? new Date(nowMs).toISOString().slice(0, 10);
}

/** A reading of this computer's clock, printed as the fixed UTC time it was (rule 6.8: never as an age that ticks). */
function fixedTime(ms: number, day: string): string {
  const iso = new Date(ms).toISOString();
  return utcStamp(iso, day)?.text ?? iso;
}

/**
 * WHY NO COMMAND BUT STOP IS OFFERED FROM WHAT THIS PAGE HOLDS OF THE RUNTIME, or null (verdict 2026-09-27 must_fix 3): the
 * snapshot read failed (the runtime is not answering), or the last snapshot reached this page longer ago than the 15 s
 * this console counts as current (a read that hangs). One predicate for every command control on the screen, Start
 * included, drawn and asked again at the click. A page that has read no snapshot yet offers nothing from its empty list
 * and needs nothing from here.
 *
 * NO TIME ON "NOT ANSWERING" (redesign r4 verify-r1 must_fix 4): the time this page's first read failed is this page's,
 * not the runtime's, and printing it made two loads over the same answers draw two different screens. The stale case
 * keeps its time: it is when the last snapshot that did reach this page arrived, which a reload has none of and says so.
 */
export function runtimeAnswerBlock(fresh: Pick<StoreSnapshot, 'sourceError' | 'links' | 'view' | 'mission'>, nowMs: number): string | null {
  const day = referenceDay(fresh.mission, nowMs);
  if (fresh.sourceError !== null) return 'the runtime is not answering';
  const at = fresh.view.snapshot_at_ms;
  if (at !== null && nowMs - at > ROBOT_ANSWER_STALE_AFTER_MS) {
    return `the runtime has not answered since its last snapshot reached this page at ${fixedTime(at, day)}`;
  }
  return null;
}

/** The read-only codes a reload of the page answers: a credential the server stopped accepting, a replaced runtime. */
const RELOAD_ANSWERS: ReadonlySet<string> = new Set(['credential_superseded', 'runtime_session_changed']);

/**
 * WHY NO CONTROL HERE COMMANDS, for a tab that may not: a recording on the screen, a contract this console does not
 * read, or the server's own reason. One sentence for every screen that draws command controls. When the reason is one a
 * reload answers (the console server stopped accepting this tab's credential, or the runtime was replaced), it says so
 * (redesign r4 verify-r1 must_fix 2): the page cannot get the new credential by asking, only by loading again.
 */
export function readOnlyWords(
  view: Pick<ViewState, 'contract_compatible' | 'commands_enabled' | 'read_only_reason'>,
  replaying: boolean,
  label: (code: string | null) => string = (code) => textOr(code),
  handshakeAnswered = true,
): string | null {
  if (replaying) return 'a recorded run is on the screen, so no control here commands the robot';
  if (!view.contract_compatible) return 'the runtime speaks a contract version this console does not read';
  if (view.commands_enabled) return null;
  // A HANDSHAKE THE RUNTIME HAS NOT ANSWERED: the API turned commands off because the runtime is not answering, which is
  // what every control says then (verify-r1 must_fix 3 and 4), never "the server does not allow this tab".
  if (!handshakeAnswered) return 'the runtime is not answering';
  const reload = view.read_only_reason !== null && RELOAD_ANSWERS.has(view.read_only_reason) ? '; reload this page to command the runtime that is answering now' : '';
  return `the server does not allow this tab to command: ${label(view.read_only_reason)}${reload}`;
}

/** Whether a command moves the robot: every command capability but observe (demo_runtime.read_models.MOTION_CAPABILITIES). */
export function movesTheRobot(capabilityId: string): boolean {
  return capabilityId !== 'observe';
}

/**
 * WHY A COMMAND THAT MOVES THE ROBOT IS OFF BESIDE A MOTION HOLD, or null: the robot's motion permission, read as the
 * motion-hold banner and Start read it (a permission read from a retained robot answer is not a current one). The list
 * masks a hold during a mission; the runtime refuses on its own too, and this keeps the control from being offered.
 */
export function motionHeldBlock(fresh: Pick<StoreSnapshot, 'robot' | 'robotError' | 'robotReadAtMs'>, capabilityId: string, nowMs: number): string | null {
  if (!movesTheRobot(capabilityId)) return null;
  const permission = motionPermissionView({ status: fresh.robot, error: fresh.robotError, readAtMs: fresh.robotReadAtMs }, nowMs);
  if (!permission.applies || permission.permitted) return null;
  return `robot motion is held: ${permission.why ?? permission.code ?? 'the robot gave no reason'}`;
}

/**
 * WHY A CONTROL OFFERED FROM THE MISSION RECORD IS OFF WHILE THAT RECORD IS NOT CURRENT, or null (spec 4.1 Stale): the last
 * mission read failed, or the last good one reached this page longer ago than 15 s. The record on the screen is then not
 * a statement that its step is still the one to take. Null when no mission was ever read: nothing is offered from one.
 */
export function missionReadBlock(fresh: Pick<StoreSnapshot, 'mission' | 'missionError' | 'missionReadAtMs'>, nowMs: number): string | null {
  const at = fresh.missionReadAtMs;
  if (at === null) return null;
  const when = fixedTime(at, referenceDay(fresh.mission, nowMs));
  if (fresh.missionError !== null) return `the last mission read failed (${fresh.missionError}), and the mission record on this page is the one read at ${when}`;
  if (nowMs - at > ROBOT_ANSWER_STALE_AFTER_MS) return `the mission record on this page was read at ${when} and is not current`;
  return null;
}

/**
 * WHY A CONTROL OF THE CAKE REGION IS OFF WHILE THE ROBOT STATUS ON THIS PAGE IS NOT CURRENT, or null: the condition the
 * demo screen marks the region "Not read since" for (a failed robot read, or a robot link that is stale), in the robot
 * link's own words. Null when no robot status was ever read.
 */
export function robotReadBlock(fresh: Pick<StoreSnapshot, 'robot' | 'robotError' | 'robotReadAtMs'>, nowMs: number): string | null {
  if (fresh.robot === null) return null;
  const link = robotLink({ status: fresh.robot, error: fresh.robotError, readAtMs: fresh.robotReadAtMs }, nowMs);
  if (fresh.robotError === null && !link.stale) return null;
  return `the robot status on this page is not current: ${link.detail}`;
}

/** A number typed by the operator, and why it cannot be used yet. Nothing is prefilled: there is no sample value. */
export interface Entered {
  value: number | null;
  missing: string | null;
}

export function entered(raw: string, unit: string, low: number, high: number, nonZero: boolean): Entered {
  if (raw.trim() === '') return { value: null, missing: `no value has been entered (${unit}, between ${String(low)} and ${String(high)})` };
  const value = Number(raw);
  if (!Number.isFinite(value)) return { value: null, missing: `that is not a number (${unit})` };
  if (nonZero && value === 0) return { value: null, missing: 'zero is not a step the controller accepts' };
  if (value < low || value > high) return { value: null, missing: `${String(value)} ${unit} is outside the controller bound of ${String(low)} to ${String(high)}` };
  return { value, missing: null };
}

/** A signed magnitude bound, where a value between zero and the smallest accepted step is refused as well. */
export function enteredSigned(raw: string, unit: string, smallest: number, largest: number): Entered {
  if (raw.trim() === '') return { value: null, missing: `no value has been entered (${unit}, ${String(smallest)} to ${String(largest)} either way)` };
  const value = Number(raw);
  if (!Number.isFinite(value)) return { value: null, missing: `that is not a number (${unit})` };
  const size = Math.abs(value);
  if (size < smallest || size > largest) {
    return { value: null, missing: `${String(value)} ${unit} is outside the validated range of ${String(smallest)} to ${String(largest)} either way` };
  }
  return { value, missing: null };
}

/**
 * What a preview was taken against. Execute is only offered while every one of these still reads the same: a plan is
 * about one runtime, one owner, one controller state and one set of parameters, and it says nothing about another.
 */
export interface PreviewBinding {
  runtime_session: string | null;
  owner_kind: string;
  owner_run_id: string | null;
  lease_generation: number | null;
  machine_state: string | null;
  parameters: string;
}

export function previewBinding(view: ViewState, parameters: string): PreviewBinding {
  return {
    runtime_session: view.runtime_session,
    owner_kind: view.owner.kind,
    owner_run_id: view.owner.run_id,
    lease_generation: view.lease?.generation ?? view.owner.lease?.generation ?? null,
    machine_state: view.machine?.state ?? null,
    parameters,
  };
}

/** Which part of the binding moved, in words, or null while the preview still describes the robot in front of you. */
export function bindingInvalidation(taken: PreviewBinding, now: PreviewBinding): string | null {
  if (taken.parameters !== now.parameters) return 'the parameters were edited after the preview was taken';
  if (taken.runtime_session !== now.runtime_session) return 'the runtime this console is reading is not the one the preview was taken against';
  if (taken.owner_kind !== now.owner_kind || taken.owner_run_id !== now.owner_run_id) return 'who holds the robot changed after the preview was taken';
  if (taken.lease_generation !== now.lease_generation) return 'the host command lease was taken again after the preview';
  if (taken.machine_state !== now.machine_state) return 'the controller left the state the preview was planned from';
  return null;
}

/** One plan-only answer this console holds, and the binding it is only valid under. */
export interface TakenPreview {
  command_id: string;
  status: string;
  reason_code: string | null;
  /** The digest the runtime returned for the payload it accepted, when it returned one. */
  payload_digest: string | null;
  at_ms: number;
  binding: PreviewBinding;
}

/** The statuses a plan-only answer may carry and still be something to execute against. */
const PREVIEW_ACCEPTED: ReadonlySet<string> = new Set(['accepted', 'spawning', 'running', 'finished']);

/** Why the preview cannot be executed against, or null when it can. */
export function previewRefusal(preview: TakenPreview | null, now: PreviewBinding, nowMs: number): string | null {
  if (preview === null) return 'nothing has been previewed yet: preview the plan first, and this executes the plan that came back';
  if (!PREVIEW_ACCEPTED.has(preview.status)) {
    return `the runtime answered the preview with ${preview.status}${preview.reason_code === null ? '' : `: ${preview.reason_code}`}`;
  }
  const moved = bindingInvalidation(preview.binding, now);
  if (moved !== null) return `${moved}, so the preview no longer describes what this would do`;
  if (nowMs - preview.at_ms > PREVIEW_STALE_AFTER_MS) {
    return `the preview was taken more than ${millis(PREVIEW_STALE_AFTER_MS)} ago and is not treated as current`;
  }
  return null;
}

/**
 * Why a second start is refused in this browser, or null.
 *
 * A start whose answer has not come back yet, and a start whose answer was lost, are both commands that may be
 * running. A second one is a second run, and it is refused here before a request is built rather than left to the
 * server to reject after the fact.
 */
export function startRefusal(commands: CommandTrack[]): string | null {
  for (const track of commands) {
    if (track.type !== 'start_cycle') continue;
    if (track.unresolved !== null || track.result?.status === 'uncertain') {
      return `a start whose execution is NOT known is already on the record (${track.command_id.slice(0, 10)}); ask the runtime what became of it first`;
    }
    if (track.result === null) {
      return `a start sent from this tab has not been answered yet (${track.command_id.slice(0, 10)}); a second one would be a second run`;
    }
  }
  return null;
}

/**
 * THE ONE THING THIS BROWSER REMEMBERS ACROSS A RELOAD: the id of a command that may still be running, and which
 * runtime it was sent to.
 *
 * A reload loses the command track, and a command whose answer was lost is exactly the one an operator must be able
 * to ask about afterwards. Only the id and the runtime session are written: the per-launch credential the API injects
 * into the page is held in memory by the source and never reaches storage, and nothing here carries a run's contents.
 * A remembered id whose runtime session is not the one now answering is dropped rather than shown, because an id
 * counts in the log of the runtime that minted it and means nothing in another.
 */
export interface PendingCommand {
  command_id: string;
  command_type: string;
  runtime_session: string | null;
}

const PENDING_KEY = 'kiwi-console-pending-command';

export function rememberPending(storage: Storage | null, pending: PendingCommand): void {
  if (storage === null) return;
  try {
    storage.setItem(PENDING_KEY, JSON.stringify(pending));
  } catch {
    // Storage can be unavailable or full. A command id that could not be written is not worth failing a send for.
  }
}

export function forgetPending(storage: Storage | null): void {
  if (storage === null) return;
  try {
    storage.removeItem(PENDING_KEY);
  } catch {
    // As above: nothing on the screen depends on the removal succeeding.
  }
}

export function readPending(storage: Storage | null, runtimeSession: string | null): PendingCommand | null {
  if (storage === null) return null;
  let raw: string | null = null;
  try {
    raw = storage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const record = parsed as Record<string, unknown>;
  const commandId = typeof record['command_id'] === 'string' ? record['command_id'] : null;
  const commandType = typeof record['command_type'] === 'string' ? record['command_type'] : null;
  const session = typeof record['runtime_session'] === 'string' ? record['runtime_session'] : null;
  if (commandId === null || commandId === '' || commandType === null) return null;
  if (runtimeSession !== null && session !== runtimeSession) return null;
  return { command_id: commandId, command_type: commandType, runtime_session: session };
}

/** The session storage of this browser, or null where there is none (a server render, or storage turned off). */
export function browserStorage(): Storage | null {
  try {
    return typeof globalThis.sessionStorage === 'undefined' ? null : globalThis.sessionStorage;
  } catch {
    return null;
  }
}
