// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE THREE THINGS THIS SCREEN COMMANDS, and the checks that stay behind them.
//
// This replaces a panel that offered seven controls, a holding declaration beside four of them and a paragraph under
// each. What is left is the one sequence the milestone is about: look at the scene, run the whole cycle, put the arm
// back. Stop is not here; it is in the header, where it is visible whatever the screen is showing.
//
// NOTHING WAS RELAXED ON THE WAY. Every refusal the old panel made is still made here, in the same order and by the
// same functions:
//
//   THE BLOCK FIRST. While the runtime holds a command whose execution is not known, every motion control here is off
//   with that reason, whatever the capability list says. The list is a snapshot and can be seconds old; the block is
//   the one condition under which a stale enabled must never win. `blockedByUncertainty` decides it and it is asked
//   for every control, by the one function that draws them.
//   THEN THE SERVER'S OWN LIST, in the server's own words, including the sentence it sends with a capability it could
//   not determine. An `unknown` capability is never drawn as ready.
//   THEN THE LEASE, where the runtime says one is required and it does not hold a current one.
//   THEN THE HOLD FOR THE OPERATOR'S DECISION: the runtime takes only Stop while a mission is carried, and during a
//   hold a control is on only when the hold's own next actions list it (`holdBlock`), which they never do for these.
//   THEN THE ROBOT ANSWER THAT STOPPED BEING REFRESHED, which is the one refusal the runtime cannot make for itself.
//   THEN A SECOND START while the first has no answer, which is refused in this browser before a request is built.
//
// And all of it AGAIN at the moment of the click, against a freshly read store snapshot, because the list on the
// screen is up to five seconds old and the question at the click is what is true now.
//
// THE OPERATOR IS ASKED ONCE, AT THE ACTION THAT NEEDS IT. Observe carries a `holding` argument with no default; when
// the record settles it, nothing is asked at all. When it does not, the question is asked in the one place it is
// needed instead of standing permanently beside four controls. A live start asks for the physical checks once, in a
// dialog, and that dialog is also where the one sentence about attachment belongs: a check that passed on this laptop
// is not the robot agreeing to attach the arm.

import type { ReactNode } from 'react';
import { useId, useState } from 'react';
import type { Capability, Command } from '@kiwi/demo-contracts';
import { textOr } from '../model/format';
import { reasonCodeLabel } from '../model/reason';
import { ATTACH_IS_DECIDED_ON_THE_ROBOT } from '../model/robot';
import { motionPermissionView } from '../model/robotStatus';
import { heldForDecision, RETRY_ALREADY_ASKED, stoppedMissionReason } from '../model/failureBanner';
import { holdBlock, holdOffer } from '../model/missionView';
import { blockedByUncertainty } from '../model/uncertain';
import type { UnresolvedCommand } from '../api/types';
import type { ViewState } from '../model/types';
import type { MissionRecord } from '../model/missionRecord';
import type { CommandTrack, StoreSnapshot } from '../store/store';
import type { ConsoleStore } from '../store/store';
import { useNowMs } from '../store/useConsole';
import { Dialog } from '../ui/Dialog';
import {
  HOLDING_COMMANDS,
  authorityBlock,
  browserStorage,
  commandPending,
  currentHoldingMission,
  deriveHolding,
  forgetPending,
  missionReadBlock,
  motionHeldBlock,
  readOnlyWords,
  readPending,
  rememberPending,
  robotBlock,
  missionEndedNotIdle,
  runEndedNotIdle,
  runInProgressBlock,
  runtimeAnswerBlock,
  startRefusal,
} from './commandGuards';
import type { HoldingReading, PendingCommand } from './commandGuards';

/** What somebody at the robot states before a live cycle. Asked once, at the start, and never repeated on the screen. */
export const ATTESTATIONS = [
  'I am at the robot and the servo power switch is within reach.',
  'The floor in front of the robot is clear.',
  'The cube and the pad are staged as the run expects.',
];

export function capabilityOf(list: Capability[], id: string): Capability | null {
  return list.find((capability) => capability.id === id) ?? null;
}

/** Why the server says this control is off, in the SERVER's own words wherever it sent any. */
function blockedReason(capability: Capability | null): string | null {
  if (capability === null) return 'this runtime does not report this capability';
  if (capability.state === 'enabled') return null;
  const stated = capability.reason ?? reasonCodeLabel(capability.reason_code);
  if (capability.state === 'unknown') return stated ?? 'the runtime has not determined whether this is available';
  return textOr(stated);
}

/** The block first, then the server's list, then the lease. The order is the point. */
export function offReason(
  unresolved: UnresolvedCommand | null,
  capabilityId: string,
  capability: Capability | null,
  authority: string | null,
): string | null {
  const blocked = blockedByUncertainty(unresolved, capabilityId);
  if (blocked !== null) return blocked;
  return blockedReason(capability) ?? authority;
}

/**
 * `offReason`'s answer, with the snapshot's `run_active` said as this display knows it once the record holds the end of
 * the run it names (`runEndedNotIdle`, redesign r4 verify-r2 must_fix 2), and its `mission_running` once the mission
 * record this display holds has ended (`missionEndedNotIdle`, verify-r3 must_fix 1). The uncertain command's block is
 * never restated, and a control that is off stays off: only the words of those two stale reasons change.
 */
function sayRunEnded(view: ViewState, mission: MissionRecord | null, capabilityId: string, reason: string | null): string | null {
  if (reason === null || blockedByUncertainty(view.unresolved, capabilityId) !== null) return reason;
  const capability = capabilityOf(view.capabilities, capabilityId);
  return runEndedNotIdle(view, capability) ?? missionEndedNotIdle(capability, mission) ?? reason;
}

/**
 * `offReason` over one view, as every screen's Start and the readiness line read it (`sayRunEnded`), with the mission
 * record the screen holds (null when it holds none).
 */
export function capabilityOffReason(view: ViewState, capabilityId: string, mission: MissionRecord | null): string | null {
  return sayRunEnded(view, mission, capabilityId, offReason(view.unresolved, capabilityId, capabilityOf(view.capabilities, capabilityId), authorityBlock(view)));
}

/**
 * WHY ONE CONTROL OF THIS PANEL IS OFF NOW, in the order the panel asks: a runtime that is not answering or whose snapshot
 * is not current (`runtimeAnswerBlock`: the list on the screen is then from before), the command whose execution is not
 * known, the server's list, the lease, then a run the list does not show yet (a command from this tab that is not
 * settled, or a run on the record: `runInProgressBlock`), then the hold the runtime serves (a control is on beside a hold
 * only when the hold's next actions list it, `holdBlock`), then the robot answer that stopped being current, the robot's
 * motion permission for a command that moves it, and for Return arm a mission record that is not current. Read for the
 * drawn control, by the failure banner for the Return arm it draws, and again at the click, so the two cannot differ.
 * The read-only reasons are the caller's.
 */
export function controlOffReason(fresh: StoreSnapshot, capabilityId: string, nowMs: number): string | null {
  const reason = offReason(fresh.view.unresolved, capabilityId, capabilityOf(fresh.view.capabilities, capabilityId), authorityBlock(fresh.view));
  return runtimeAnswerBlock(fresh, nowMs) ?? sayRunEnded(fresh.view, fresh.mission, capabilityId, reason) ?? afterTheList(fresh, capabilityId, nowMs);
}

/**
 * THE REFUSALS AFTER THE SERVER'S LIST AND THE LEASE, in order, asked by the drawn control and by the click alike, so the
 * two cannot drift: a run the list does not show yet, the hold, the robot answer that stopped being current, the motion
 * hold (the banner beside the control says it is off, and so does the control), and for Return arm, which the mission
 * card offers from the mission's step list, a mission record that is not current (spec 4.3, Return arm: Stale disabled).
 */
function afterTheList(fresh: StoreSnapshot, capabilityId: string, nowMs: number): string | null {
  return (
    runInProgressBlock(fresh.view, commandPending(fresh)) ??
    holdBlock(fresh.mission, capabilityId) ??
    robotBlock(fresh.view, nowMs) ??
    motionHeldBlock(fresh, capabilityId, nowMs) ??
    (capabilityId === 'return_arm' ? missionReadBlock(fresh, nowMs) : null)
  );
}

/**
 * The capability list is READ AGAIN at the moment of the click. The list on the screen is up to one poll old and the
 * question here is not what was drawn, it is what is true now.
 *
 * It sits at module scope, and is exported, so that the Start control in the imported design's header and the controls
 * in this panel make the SAME refusals from the SAME function. A second copy of this reasoning somewhere else is how
 * one of them ends up a check short.
 */
export function submitRefusal(fresh: StoreSnapshot, capabilityId: string): string | null {
  if (fresh.replay !== null) return 'the screen moved to a recorded run before this was sent, so nothing was sent';
  if (!fresh.view.contract_compatible) return 'the runtime stopped speaking a contract version this console reads, so nothing was sent';
  if (!fresh.view.commands_enabled) return 'the server stopped allowing this tab to command, so nothing was sent';
  const reason = controlOffReason(fresh, capabilityId, Date.now());
  if (reason !== null) return `this changed between the screen and the click, so nothing was sent: ${reason}`;
  if (capabilityId === 'start_cycle') {
    const second = startRefusal(fresh.commands);
    if (second !== null) return second;
  }
  return null;
}

/**
 * WHY TRY AGAIN IS OFF, or null when it may be sent. It is a motion door (the runtime parks the arm with the proven
 * exit and then starts a fresh attempt), so it makes the refusals Start and every control here make, from the SAME
 * functions: the uncertain-command block, the lease, the robot answer that stopped being current. It also reads the
 * robot's motion permission, which the capability list masks during a hold. Then what is about the hold itself: a
 * failed mission read leaves the last record on the screen, and a record nobody could refresh does not say the mission
 * is still held; the hold must be there, and not already stopping.
 *
 * The capability list is not read for it. The runtime turns `start_cycle` off while any mission is carried, a held one
 * included, and the retry is the mission's own door, which the runtime admits on its own terms.
 *
 * Called for the drawn button with the drawn snapshot and AGAIN at the click with a freshly read one.
 */
export function retryRefusal(fresh: StoreSnapshot, nowMs: number): string | null {
  if (fresh.replay !== null) return 'a recorded run is on the screen, so no control here commands the robot';
  if (!fresh.view.contract_compatible) return 'the runtime speaks a contract version this console does not read';
  if (!fresh.view.commands_enabled) return 'the server does not allow this tab to command';
  const guard =
    runtimeAnswerBlock(fresh, nowMs) ?? blockedByUncertainty(fresh.view.unresolved, 'start_cycle') ?? authorityBlock(fresh.view) ?? robotBlock(fresh.view, nowMs);
  if (guard !== null) return guard;
  // THE ROBOT'S MOTION PERMISSION, read here and not from the capability list: during a hold the runtime reports the
  // mission as the reason every motion control is off, which masks a motion hold. This is the reading the motion-hold
  // banner draws, so Try again is never on beside a banner that says motion is held. The runtime refuses on its own too.
  const permission = motionPermissionView({ status: fresh.robot, error: fresh.robotError, readAtMs: fresh.robotReadAtMs }, nowMs);
  if (permission.applies && !permission.permitted) {
    return `robot motion is held: ${permission.why ?? permission.code ?? 'the robot gave no reason'}`;
  }
  if (fresh.missionError !== null) {
    return `the last mission read failed (${fresh.missionError}), so whether the mission is still held for a decision is not known here`;
  }
  // A HOLD READ LONGER AGO THAN 15 S is not a statement that the mission is still held (spec 4.3, Try again: Stale disabled).
  const missionOld = missionReadBlock(fresh, nowMs);
  if (missionOld !== null) return missionOld;
  const stopped = stoppedMissionReason(fresh.mission);
  if (stopped !== null) return stopped;
  if (!heldForDecision(fresh.mission)) return 'the mission is not held for a decision, so there is no attempt to retry';
  if (fresh.mission?.stop_requested === true) return `mission ${fresh.mission.mission_id} is stopping: its stop was asked for`;
  if (fresh.startingMission) return 'a mission start from this tab has not been answered yet';
  // THE HOLD'S OWN RECORD SAYS WHETHER A RETRY IS OFFERED: never sent when its next actions do not carry one.
  const offer = holdOffer(fresh.mission);
  if (offer === 'stop_only') return 'the runtime offers only Stop for this hold';
  if (offer === 'not_served' || offer === null) return 'the runtime served no record of the held attempt, so no retry is offered';
  // A retry already written for this attempt (a press from another tab, or this one before the record came back): the
  // runtime refuses a second (mission_retry_already_requested), so it is not offered.
  if (fresh.mission?.hold?.retry_requested === true) return RETRY_ALREADY_ASKED;
  return null;
}

/** The scene scan as a contract command (track P0b): pan-only views from the low scan pose. It carries no `holding`. */
export function scanSceneCommand(): Command {
  return { type: 'scan_scene', args: { plan_only: false } };
}

/** One thing this console can ask the runtime to do. */
interface ActionSpec {
  /** The capability id the server drives this control by. */
  id: string;
  /** The label on the button. Direct, and the same words the step guide uses. */
  label: string;
  /** One short line about what it does. Not a paragraph. */
  summary: string;
  /** True for the commands whose contract arguments carry `holding`, which has no default. */
  needsHolding: boolean;
  /** True for the one control that asks the operator to state the physical checks first, on a live runtime. */
  attests: boolean;
  build: (holding: boolean | null) => Command | null;
}

function ActionControl(props: {
  spec: ActionSpec;
  primary: boolean;
  reason: string | null;
  busy: boolean;
  refusal: string | null;
  unresolved: UnresolvedCommand | null;
  /** `missionUnread`: the code of a mission read that failed before any mission was read (the record that settles holding), or null. */
  /** `missionPast`: why the served mission, History's, was not read for holding, with its date; or null. */
  holding: { needed: boolean; reading: HoldingReading; choice: 'held' | 'empty' | null; missionUnread?: string | null; missionPast?: string | null };
  onHolding: (choice: 'held' | 'empty') => void;
  onRun: () => void;
}): ReactNode {
  const spec = props.spec;
  const holdingUnsettled = props.holding.needed && props.holding.reading.value === null && props.holding.choice === null;
  const off = props.reason !== null || holdingUnsettled;
  const disabled = off || props.busy;
  // The sentence that says why the control is off is the control's description, so a screen reader reads it with the
  // button and a DOM check can find it from the button alone.
  const reasonId = useId();
  return (
    <div className="action">
      <button
        type="button"
        className={`button ${props.primary ? 'button--primary' : ''} action__button`}
        data-control={spec.id}
        data-primary={props.primary ? 'true' : undefined}
        disabled={disabled}
        aria-disabled={disabled ? 'true' : 'false'}
        aria-describedby={off ? reasonId : undefined}
        onClick={props.onRun}
      >
        {props.busy ? `${spec.label}…` : spec.label}
      </button>
      <p className="action__summary">{spec.summary}</p>
      {props.reason === null ? null : (
        <p className="action__off" id={reasonId} data-control-reason={spec.id}>
          Off: {props.reason}
        </p>
      )}
      {/* WHY THE BUTTON IS OFF, BEFORE THE CLICK AND IN THE SAME WORDS AS EVERY OTHER REFUSAL ON THIS SCREEN.
          A missing holding declaration used to disable this button with no "Off:" line at all, because it is not one
          of the runtime's reasons: the operator saw a dead button, a sentence that read as an observation about the
          record, and two buttons that did not look like the thing standing between them and the action. It is a
          refusal like any other and it is now written as one, with the single choice that clears it underneath. */}
      {props.reason === null && holdingUnsettled ? (
        <p className="action__off" id={reasonId} data-control-reason={spec.id}>
          Off until you state whether the gripper is holding the cube.
        </p>
      ) : null}
      {/* Asked ONCE, here, and only while the record does not settle it. Not a row of four choices beside every
          control, which is what this screen had before: one question, at the one action whose meaning depends on it. */}
      {props.reason === null && holdingUnsettled ? (
        <div className="action__ask">
          <span>
            {props.holding.reading.basis !== null
              ? `The record leaves it unknown whether the gripper is holding the cube (${props.holding.reading.basis})`
              : (props.holding.missionUnread ?? null) !== null
                ? // A MISSION NOT READ IS NOT A RECORD THAT SAYS NOTHING (status 2026-09-27, LOW 4).
                  `The mission record, which settles whether the gripper is holding the cube, has not been read (${props.holding.missionUnread ?? ''})`
                : (props.holding.missionPast ?? null) !== null
                  ? // A MISSION THAT IS HISTORY'S, SET ASIDE AND SAID WITH ITS DATE (verify-r3 M3).
                    (props.holding.missionPast ?? '')
                  : 'No record read here says whether the gripper is holding the cube'}
            , and the controller runs a different check depending on the answer, so it will not take an observation until it
            is told which. State it once:
          </span>
          <span className="action__ask-buttons">
            <button type="button" className="button" onClick={() => props.onHolding('held')}>
              holding the cube
            </button>
            <button type="button" className="button" onClick={() => props.onHolding('empty')}>
              empty
            </button>
          </span>
        </div>
      ) : null}
      {/* READ, NEVER ASKED, WHEN A RECORD SETTLES IT (spec 5.7.1): what the command will carry, and the record it rests on. */}
      {props.reason === null && props.holding.needed && props.holding.reading.value !== null ? (
        <p className="action__note" data-holding-basis="">
          Sent as: {props.holding.reading.value ? 'Holding the block' : 'Gripper empty'}, from {props.holding.reading.basis ?? 'the record'}.
        </p>
      ) : null}
      {props.holding.needed && props.holding.reading.value === null && props.holding.choice !== null ? (
        <p className="action__note">Stated on this screen: the gripper is {props.holding.choice === 'held' ? 'holding the cube' : 'empty'}.</p>
      ) : null}
      {props.refusal === null ? null : <p className="action__off">{props.refusal}</p>}
    </div>
  );
}

/**
 * `omit` lets a caller leave out a control it draws itself, without a second copy of the guards existing anywhere.
 *
 * The imported design puts Start in the header, where it is reachable whatever the screen is showing, and leaves this
 * panel to the secondary interface. The header's Start is now the MISSION start (`design/startPress.ts`), which makes
 * the same refusals through `submitRefusal` below and then the mission's own; `start_cycle` is kept in the specs for
 * the callers and tests that draw the whole panel, and the operator screen draws only Observe and Return arm from it.
 * Omitting nothing is the default and is what every existing caller and test gets.
 */
/**
 * `only` draws JUST the named controls and `omit` drops them. They exist so the SAME panel, with the same five
 * refusal layers, the same holding question and the same confirmation dialog, can be placed beside a failed result
 * without a second implementation of any of it. A control drawn here is the control drawn in Diagnostics.
 */
export function Actions(props: {
  snapshot: StoreSnapshot;
  store: ConsoleStore;
  omit?: readonly string[];
  only?: readonly string[];
  heading?: string;
  /** The control drawn as the primary one. Start pick & place unless a caller names another; null draws none primary. */
  primary?: string | null;
}): ReactNode {
  const { view, commands } = props.snapshot;
  const replaying = props.snapshot.replay !== null;
  const readOnly = replaying || !view.contract_compatible || !view.commands_enabled;
  const readOnlyReason = readOnlyWords(view, replaying, (code) => textOr(reasonCodeLabel(code)), props.snapshot.handshake.state !== 'not_answered') ?? '';

  const [holdingChoice, setHoldingChoice] = useState<Record<string, 'held' | 'empty'>>({});
  const [refusals, setRefusals] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [attesting, setAttesting] = useState(false);
  const [checked, setChecked] = useState<boolean[]>(ATTESTATIONS.map(() => false));
  const storage = browserStorage();
  const [pending, setPending] = useState<PendingCommand | null>(() => readPending(storage, null));

  const unresolved = replaying ? null : view.unresolved;
  const authority = replaying ? null : authorityBlock(view);
  const nowMs = useNowMs();
  // A MISSION THAT IS HISTORY'S DOES NOT SETTLE HOLDING (verify-r3 M3): its stop state is that mission's end, not now.
  const holdingSource = currentHoldingMission(props.snapshot, nowMs);
  const holdingReading = deriveHolding(view, holdingSource.mission);
  const missionError = props.snapshot.missionError;
  const missionUnread = props.snapshot.mission === null && missionError !== null && missionError !== 'no_mission_recorded' ? missionError : null;
  const simulation = view.data_mode === 'synthetic';
  // A remembered command id counts in the log of the runtime that minted it. One bound to another runtime session is
  // not shown at all: the runtime restarted and the id means nothing in the new one.
  const pendingHere = pending !== null && (view.runtime_session === null || pending.runtime_session === view.runtime_session) ? pending : null;

  const specs: ActionSpec[] = [
    {
      id: 'scan_scene',
      label: 'Scan scene',
      // THE MOVEMENT SCOPE, stated on the control: the arm pans through the scan views from the low scan pose.
      summary: 'Pans the ARM through the scan views from the low scan pose and fuses what they saw. The base is not driven and the gripper is not used.',
      needsHolding: false,
      attests: false,
      build: () => scanSceneCommand(),
    },
    {
      id: 'observe',
      label: 'Observe scene',
      summary: 'One capture and one perception pass. The robot does not move.',
      // WHETHER A COMMAND CARRIES `holding` IS ASKED OF THE SHARED LIST, not repeated here. `HOLDING_COMMANDS` names
      // the commands whose contract arguments carry it; it was exported and read by nothing, so a control could
      // disagree with the list that documents the rule. Now the list is what decides, for every control.
      needsHolding: HOLDING_COMMANDS.includes('observe'),
      attests: false,
      build: (holding) => (holding === null ? null : { type: 'observe', args: { holding } }),
    },
    {
      id: 'start_cycle',
      label: 'Start pick & place',
      summary: 'The whole cycle, once: align, grasp, lift, carry, place, return. There is nothing to press in between.',
      needsHolding: HOLDING_COMMANDS.includes('start_cycle'),
      attests: true,
      build: () => ({ type: 'start_cycle', args: { plan_only: false } }),
    },
    {
      id: 'return_arm',
      label: 'Return arm',
      // THE MOVEMENT SCOPE, stated on the control. `return_to_low_pose` commands the four scan joints to the saved
      // low scan pose and then re-commands the pose the arm actually reached, so every goal equals its measured
      // position. It moves the ARM only: the base is not driven and the gripper is not opened or closed.
      summary: 'Moves the ARM only, to the saved low scan pose, then settles on the pose it reached. The base is not driven and the gripper is not opened or closed.',
      needsHolding: HOLDING_COMMANDS.includes('return_arm'),
      attests: false,
      build: () => ({ type: 'return_arm', args: { plan_only: false } }),
    },
  ];

  const run = (spec: ActionSpec): void => {
    void (async () => {
      const fresh = props.store.getSnapshot();
      const refusal = submitRefusal(fresh, spec.id);
      const pressed = spec.id === 'start_cycle' ? 'start' : spec.id === 'return_arm' || spec.id === 'observe' || spec.id === 'scan_scene' ? spec.id : null;
      if (refusal !== null) {
        setRefusals((current) => ({ ...current, [spec.id]: refusal }));
        // Refused at the click, before anything was sent: still a press on this display (spec rule 6.5).
        if (pressed !== null) props.store.refusedPress(pressed, `Refused at the click, nothing was sent: ${refusal}`);
        return;
      }
      const stated = holdingChoice[spec.id];
      const derived = deriveHolding(fresh.view, currentHoldingMission(fresh, Date.now()).mission).value;
      const holding = spec.needsHolding ? (derived ?? (stated === undefined ? null : stated === 'held')) : null;
      if (spec.needsHolding && holding === null) {
        setRefusals((current) => ({ ...current, [spec.id]: 'holding is not settled, so nothing was sent' }));
        return;
      }
      // What the screen showed is what is sent, or nothing is. A record that settled holding differently between the
      // render and the click changes what this command means.
      if (spec.needsHolding && holdingReading.value !== null && holding !== holdingReading.value) {
        setRefusals((current) => ({
          ...current,
          [spec.id]: 'the record settled holding differently between the screen and the click, so nothing was sent',
        }));
        return;
      }
      const command = spec.build(holding);
      if (command === null) return;
      setRefusals((current) => {
        const next = { ...current };
        delete next[spec.id];
        return next;
      });
      setBusy(spec.id);
      let track: CommandTrack;
      try {
        track = await props.store.submit(command);
      } finally {
        setBusy(null);
      }
      const remembered: PendingCommand = { command_id: track.command_id, command_type: track.type, runtime_session: fresh.view.runtime_session };
      if (track.result === null) {
        rememberPending(storage, remembered);
        setPending(remembered);
      } else {
        forgetPending(storage);
        setPending(null);
      }
    })();
  };

  const startSpec = specs.find((spec) => spec.id === 'start_cycle') as ActionSpec;
  const allChecked = checked.every((item) => item);

  const control = (spec: ActionSpec): ReactNode => {
    const capability = capabilityOf(view.capabilities, spec.id);
    // Before the list, a runtime that is not answering; after the list and the lease, the refusals the click makes too
    // (`afterTheList`), in the click's order (`controlOffReason`). A replay is read-only and says so below.
    const answering = replaying ? null : runtimeAnswerBlock(props.snapshot, nowMs);
    const after = replaying ? null : afterTheList(props.snapshot, spec.id, nowMs);
    const reason =
      spec.id === 'start_cycle'
        ? answering ?? sayRunEnded(view, props.snapshot.mission, spec.id, offReason(unresolved, spec.id, capability, authority)) ?? after ?? (replaying ? null : startRefusal(commands))
        : answering ?? sayRunEnded(view, props.snapshot.mission, spec.id, offReason(unresolved, spec.id, capability, authority)) ?? after;
    return (
      <ActionControl
        key={spec.id}
        spec={spec}
        primary={props.primary === null ? false : spec.id === (props.primary ?? 'start_cycle')}
        reason={readOnly ? readOnlyReason : reason}
        busy={busy === spec.id}
        refusal={refusals[spec.id] ?? null}
        unresolved={unresolved}
        holding={{ needed: spec.needsHolding, reading: holdingReading, choice: holdingChoice[spec.id] ?? null, missionUnread, missionPast: holdingSource.past?.words ?? null }}
        onHolding={(choice) => setHoldingChoice((current) => ({ ...current, [spec.id]: choice }))}
        onRun={() => {
          if (spec.attests && !simulation) {
            setChecked(ATTESTATIONS.map(() => false));
            setAttesting(true);
            return;
          }
          run(spec);
        }}
      />
    );
  };

  return (
    <div className="actions">
      {pendingHere === null ? null : (
        <p className="actions__pending">
          A command sent from this browser has no answer: <code>{pendingHere.command_id}</code>. It is not sent again. Ask the runtime what became of
          it in Connections.
        </p>
      )}

      {specs
        .filter((spec) => !(props.omit ?? []).includes(spec.id))
        .filter((spec) => props.only === undefined || props.only.includes(spec.id))
        .map(control)}

      {attesting ? (
        <Dialog
          title="Before the cycle starts"
          onClose={() => setAttesting(false)}
          actions={
            <>
              <button type="button" className="button" onClick={() => setAttesting(false)}>
                cancel
              </button>
              <button
                type="button"
                className="button button--primary"
                disabled={!allChecked}
                onClick={() => {
                  setAttesting(false);
                  run(startSpec);
                }}
              >
                start the cycle
              </button>
            </>
          }
        >
          <p>You are stating that you checked these. The record will say attested, not verified.</p>
          {ATTESTATIONS.map((line, index) => (
            <label className="check" key={line}>
              <input
                type="checkbox"
                checked={checked[index] ?? false}
                onChange={(event) => setChecked((current) => current.map((item, at) => (at === index ? event.target.checked : item)))}
              />
              <span>{line}</span>
            </label>
          ))}
          {/* The last thing on the screen before the cycle is commanded, and the one place this sentence belongs. */}
          <p className="muted">{ATTACH_IS_DECIDED_ON_THE_ROBOT}</p>
        </Dialog>
      ) : null}
    </div>
  );
}
