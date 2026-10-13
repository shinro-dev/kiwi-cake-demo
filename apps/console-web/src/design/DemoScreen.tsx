// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DEMO SCREEN, the default view (docs/autonomous-demo/console-redesign-2026-09-27.md). The classic screen
// (`Screen.tsx`) stays behind `?screen=classic`.
//
// A flight recorder read standing up, three metres from the screen, while a robot holds a block:
//
//   THE TOP BAR: shinro, the one mode mark, the robot and its connection, the motion permission, ONE readiness verdict
//   computed from Start's own guards, Start, the one Stop, Diagnostics (`TopBar`).
//   THE SAFETY MARKS nothing may hide, under it: the strip's alerts, the command whose execution is not known, the motion
//   hold with its confirmation.
//   THE MISSION, top left: the outcome card, kept expanded until acknowledged on this display (`OutcomeCard`).
//   CAKE ON THE ROBOT, top right: every activation as its own card, the pending choice, the dated signature check, the
//   live slot (`CakeStory`).
//   THE STRIP, the prime area in the middle: the five stages, the scene map and the wrist thumbnail with Scan scene and
//   Observe (operator layout ruling 2026-09-28: the live status in the middle, the history below it).
//   THE TIMELINE, full width, below the strip: the append-only, timestamped record of the mission, grouped by run
//   (`TimelineRegion`). The regions keep these places whether or not anything runs.
//
// Everything else (history, logs, evidence, connections, Cake details) is behind Diagnostics. No fact on this screen is
// computed here: every sentence is a model function's, pinned by its own tests. Nothing here counts time: a stale or
// disconnected source is marked with the fixed time of its last good read.

import { BoundedAccount } from './BoundedAccount';
import type { ReactNode, UIEvent } from 'react';
import { useEffect, useRef, useState } from 'react';
import { stopOffReason, streamRunOpen } from '../model/failureBanner';
import { activationOffReason, missionExecution, missionStartLabel, missionStartOff } from '../model/missionView';
import { missionHold, vlaStartBlock, vlaView } from '../model/vla';
import { VlaPanel } from './VlaPanel';
import { activeTarget, targetOfRun } from '../model/missionView';
import type { MissionTargetRecord } from '../model/missionRecord';
import { cakeStory } from '../model/cakeStory';
import { FOLLOWING, follow, followStep, isAtEnd } from '../model/follow';
import type { FollowState } from '../model/follow';
import { modeMark } from '../model/modeMark';
import { staleStop } from '../model/reconnect';
import { missionSteps, nextStep } from '../model/nextSteps';
import { outcomeCard } from '../model/outcomeCard';
import { readiness } from '../model/readiness';
import { noteReason, readyChecklist } from '../model/readyChecklist';
import { motionPermissionView, robotLink } from '../model/robotStatus';
import { ROBOT_ANSWER_STALE_AFTER_MS } from '../model/robot';
import { signatureRunOff, signatureUnavailableWords } from '../model/signatureCheck';
import { runIsActive } from '../model/steps';
import { activeRunOnRecord, selectedRun, needsHydration, viewForSelectedRun } from '../model/selectedRun';
import { stageTimeline } from '../model/stageTimeline';
import type { FoldedRun } from '../model/stageTimeline';
import { utcDayOf, utcStamp } from '../model/utcTime';
import { currencyContext, currentTimelineEntries, missionCurrency, pastMissions, recordCurrency, runCurrency } from '../model/currency';
import type { CurrencyContext } from '../model/currency';
import { proxiedMediaUrl } from '../model/camera';
import { COMMAND_NOT_SETTLED, commandPending, missionReadBlock, readOnlyWords, robotBlock, robotReadBlock, runtimeAnswerBlock, startRefusal } from '../panels/commandGuards';
import { Actions, ATTESTATIONS, capabilityOffReason, retryRefusal } from '../panels/Actions';
import { StripAlerts } from '../panels/StatusStrip';
import type { ScreenMode } from '../panels/StatusStrip';
import { UncertainPanel } from '../panels/UncertainPanel';
import { useConsole, useNowMs, useStore } from '../store/useConsole';
import type { StoreSnapshot } from '../store/store';
import { Dialog } from '../ui/Dialog';
import { StopBarHeight } from '../ui/stopBar';
import { ATTACH_IS_DECIDED_ON_THE_ROBOT } from '../model/robot';
import { CakeStory } from './CakeStory';
import { cameraPanel, cameraStreamBase } from './cameras';
import type { CameraPanelModel } from './cameras';
import { Drawer } from './Drawers';
import type { DrawerId } from './Drawers';
import { OutcomeCard } from './OutcomeCard';
import { MotionHold } from './RobotParts';
import { SceneMap } from './SceneMap';
import { liveSlot } from '../model/liveSlot';
import { foldSceneMap } from '../model/sceneMap';
import { ownRecords, sceneRecords, sceneRecordsLine } from '../model/sceneRecords';
import { StageTimeline } from './StageTimeline';
import { TimelineRegion } from './TimelineRegion';
import { TopBar } from './TopBar';
import { pressRetry } from './retryPress';
import { pressStart } from './startPress';
import { keptPictureAtView } from '../model/streamDecode';
import type { KeptPicture } from '../model/streamDecode';
import { useStreamDecode } from './useStreamDecode';
import type { StreamBinding } from './useStreamDecode';
import { C, INK, SURFACE } from './theme';
import { asSentence } from './actionCard';

/** The address a frame is loaded from on this page, or null when the source serves none or the address is refused. */
export function frameAddress(snapshotSource: { frameUrl: (id: string, what: 'raw' | 'overlay', maxWidth: number) => string | null }, id: string): string | null {
  return proxiedMediaUrl(snapshotSource.frameUrl(id, 'raw', 480)).url;
}

/**
 * THE TARGET THE STAGES ARE DRAWN FOR: the active target once it has a run. Before it has one the stages draw the run on
 * the screen, and that run belongs to the target the mission recorded it against (or to none), so the target named is
 * that one and never the active one the run is not of. With no run on the screen the active target is named.
 */
export function timelineTarget(mission: StoreSnapshot['mission'], selectedRunId: string | null): MissionTargetRecord | null {
  if (mission === null) return null;
  const active = activeTarget(mission);
  if (active !== null && active.runs.length > 0) return active;
  if (selectedRunId === null) return active;
  return targetOfRun(mission, selectedRunId);
}

/**
 * THE RUNS THE STAGES ARE BUILT FROM: the target's runs this page holds a fold for, oldest first, each with the events the
 * store keeps of it (for the times). A run whose records this page does not hold is left out.
 */
export function timelineRuns(
  snapshot: StoreSnapshot,
  selectedRunId: string | null,
  selectedView: StoreSnapshot['view'],
  // THE MISSION ON THE MAIN SCREEN (spec section 12): null when the record's mission is History's.
  mission: StoreSnapshot['mission'] = snapshot.mission,
): FoldedRun[] {
  const target = timelineTarget(mission, selectedRunId);
  const cached = snapshot.runFolds;
  const withEvents = (run: FoldedRun): FoldedRun => {
    const events = snapshot.missionRunEvents[run.run_id];
    return events === undefined ? run : { ...run, events };
  };
  if (target !== null && target.runs.length > 0) {
    const out: FoldedRun[] = [];
    for (const runId of target.runs) {
      if (runId === selectedRunId) out.push(withEvents({ run_id: runId, view: selectedView }));
      else {
        const held = cached[runId];
        if (held !== undefined) out.push(withEvents({ run_id: runId, view: held }));
      }
    }
    if (out.length > 0) return out;
  }
  return selectedRunId === null ? [] : [withEvents({ run_id: selectedRunId, view: selectedView })];
}

/**
 * THE WRIST PANEL WITHOUT A PICTURE THAT IS HISTORY'S (spec section 12): the newest saved frame of an earlier runtime
 * session or an earlier day is not drawn; the cover says there is none from this session and dates the one kept. The
 * stream and a picture this page decoded are this session's and are untouched.
 */
export function currentWrist(panel: CameraPanelModel, context: CurrencyContext): CameraPanelModel {
  if (panel.stillUrl === null || panel.stillRecordedAt === null) return panel;
  const currency = recordCurrency(panel.stillRecordedAt, context);
  if (currency.current) return panel;
  const at = utcStamp(panel.stillRecordedAt, null)?.text ?? panel.stillRecordedAt;
  const when = currency.reason === 'earlier_day' ? 'today' : 'in this runtime session';
  return {
    ...panel,
    stillUrl: null,
    still: null,
    stillKind: null,
    stillRecordedAt: null,
    stillRecordedAgeMs: null,
    cover: { title: `No picture from this camera ${when}.`, note: `The newest saved frame is from ${at}, ${currency.reason === 'earlier_day' ? 'an earlier day' : 'an earlier runtime session'}; it is not shown here.` },
  };
}

/* ---- the wrist thumbnail ----------------------------------------------------------------------------------------- */

/** The camera's state word in sentence case ("Not configured"), as the design writes labels. */
export function wristWord(panel: CameraPanelModel): string {
  const lower = panel.word.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/**
 * One line under the word: the runtime's reason or the cover's title, and never an age (rule 6.8). With the kept picture
 * drawn, the line says only that the stream is not showing, so it never describes a saved frame that is not drawn.
 */
export function wristLine(panel: CameraPanelModel, keptDrawn = false): string {
  if (panel.showingVideo) return 'The stream is showing on this page.';
  // THE RUNTIME'S REASON CODE when it sent one, else the cover's title: never the note's ages.
  const reason = noteReason(panel.note);
  if (keptDrawn) return `The stream is not showing${reason === null ? '' : `: ${reason}`}.`;
  if (panel.stillUrl !== null) return `The newest saved frame, not a live picture${reason === null ? '' : `: ${reason}`}.`;
  if (reason !== null) return `No picture from this camera: ${reason}.`;
  if (panel.cover !== null) return panel.cover.title;
  return 'No picture from this camera.';
}

export function WristThumb(props: {
  panel: CameraPanelModel;
  binding: StreamBinding | null;
  controls: ReactNode;
  dimmed: boolean;
  /** The last picture this page decoded, drawn through a gap with its fixed time (`keptPictureAtView`). */
  kept?: KeptPicture | null;
  referenceDay?: string | null;
}): ReactNode {
  const panel = props.panel;
  const kept = keptPictureAtView(panel.showingVideo, props.kept ?? null, props.referenceDay ?? null);
  return (
    <section aria-label="Wrist camera" data-region="wrist" data-live-view={panel.role} style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, opacity: props.dimmed ? 0.6 : 1 }}>
      <div style={{ fontSize: 13, color: INK.quiet }}>Wrist camera</div>
      <div style={{ position: 'relative', width: '100%', maxWidth: 320, aspectRatio: '16 / 9', border: '1px solid var(--color-border)', borderRadius: 8, overflow: 'hidden', background: SURFACE.well }}>
        {panel.streamUrl !== null && props.binding !== null ? (
          <img
            key={props.binding.key}
            ref={props.binding.ref}
            onLoad={props.binding.onLoad}
            onError={props.binding.onError}
            data-camera-stream=""
            src={panel.streamUrl}
            alt={`the ${panel.role} camera stream`}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', opacity: panel.showingVideo ? 1 : 0 }}
          />
        ) : null}
        {kept === null ? null : (
          <img
            data-kept-picture=""
            src={kept.url}
            alt={`the last picture this page decoded from the ${panel.role} camera`}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', filter: 'saturate(.6)' }}
          />
        )}
        {kept === null && !panel.showingVideo && panel.stillUrl !== null ? (
          <img data-camera-still="" src={panel.stillUrl} alt={`the newest stored frame from the ${panel.role} camera`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain' }} />
        ) : null}
      </div>
      <div data-camera-chip="" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: panel.tone }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: panel.tone }}>{wristWord(panel)}</span>
      </div>
      {kept === null ? null : (
        <div data-kept-words="" data-t={kept.iso} title={kept.iso} style={{ fontSize: 12, color: INK.soft, lineHeight: 1.4 }}>
          {kept.words}
        </div>
      )}
      <div data-camera-note="" style={{ fontSize: 12, color: INK.soft, lineHeight: 1.4, overflowWrap: 'anywhere' }}>
        {wristLine(panel, kept !== null)}
      </div>
      <div className="controls-row">{props.controls}</div>
    </section>
  );
}

/**
 * THE START DIALOG'S BODY: the checks the operator confirms, and the steps done by hand the runtime lists, each a box.
 * It tells the truth about where the confirmations go: this display's press list, and not the runtime (rule 5.7.4).
 */
export function StartChecks(props: { lines: readonly string[]; checked: boolean[]; onChange: (index: number, value: boolean) => void }): ReactNode {
  return (
    <>
      <p>Your confirmations are kept in this display&apos;s press list. The runtime does not receive them.</p>
      {props.lines.map((line, index) => (
        <label className="check" key={line}>
          <input type="checkbox" checked={props.checked[index] ?? false} onChange={(event) => props.onChange(index, event.target.checked)} />
          <span>{line}</span>
        </label>
      ))}
      <p className="muted">The arm has to be acquired after every power cycle; this console cannot read whether it is.</p>
      <details>
        <summary>why</summary>
        <p className="muted">{ATTACH_IS_DECIDED_ON_THE_ROBOT}</p>
      </details>
    </>
  );
}

/** Load this page again: the way to the credential the console server accepts now for every control (`model/reconnect.ts`). */
function reloadPage(): void {
  if (typeof window === 'undefined') return;
  window.location.reload();
}

/** The fixed UTC time of a read this display made, for a stale marker; null when there was none. */
function readTime(ms: number | null, day: string | null): string | null {
  return ms === null ? null : (utcStamp(new Date(ms).toISOString(), day)?.text ?? null);
}

/* ---- the screen -------------------------------------------------------------------------------------------------- */

/**
 * WHAT A SCREEN OPENS WITH besides the record: an overlay already open, as a press would leave it. The page passes none;
 * the pins render an open drawer or the Start dialog over the server renderer with it, which has no presses and no layout.
 * `barHeight` stands for the top bar's first measured height; `attest` for the dialog after a Start press, its boxes all
 * ticked or none.
 */
export interface ScreenOpening {
  drawer?: DrawerId;
  barHeight?: number;
  attest?: { allChecked: boolean };
}

export function DemoScreen(props: { opening?: ScreenOpening }): ReactNode {
  const store = useStore();
  const snapshot = useConsole();
  const nowMs = useNowMs();
  const opening = props.opening ?? {};

  const [drawer, setDrawer] = useState<DrawerId | null>(opening.drawer ?? null);
  // The top bar's height: the drawer starts below it, so the only Stop is never under the drawer (verify-r7 M1).
  const [topBarHeight, setTopBarHeight] = useState(opening.barHeight ?? 0);
  const [stopBusy, setStopBusy] = useState(false);
  const [attesting, setAttesting] = useState(opening.attest !== undefined);
  const [checked, setChecked] = useState<boolean[]>([]);
  const [followState, setFollowState] = useState<FollowState>(FOLLOWING);
  const listRef = useRef<HTMLOListElement | null>(null);
  const seenEntries = useRef(snapshot.timeline.length);

  useEffect(() => {
    void store.start();
    return () => store.dispose();
  }, [store]);

  const selectedForHydration = selectedRun(snapshot.view, snapshot.session.runtime_replaced);
  const hydrateId = needsHydration(selectedForHydration, snapshot.view, snapshot.hydrated) ? selectedForHydration.run_id : null;
  useEffect(() => {
    if (hydrateId === null) return;
    void store.hydrateRun(hydrateId);
  }, [store, hydrateId]);

  useEffect(() => {
    if (drawer === null && !attesting) return undefined;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      setAttesting(false);
      setDrawer(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawer, attesting]);

  const view = snapshot.view;
  const replaying = snapshot.replay !== null;
  const mode: ScreenMode = replaying ? 'replay' : view.data_mode;
  const mission = snapshot.mission;
  // WHAT IS CURRENT (spec section 12): the record's mission is on this screen when it has not ended, when this page read it
  // before it ended, or when its newest record is of this runtime session and today; otherwise it is History's, and every
  // region below is drawn without it. The record itself (`mission`) still decides Start and Stop; the readiness verdict
  // reads only the mission on this screen, so a past mission's step list never becomes the top bar's words (verify-r2 M1).
  const historyEntries = snapshot.history?.ok === true ? snapshot.history.entries : [];
  const currency = currencyContext(view.runtime_session, nowMs);
  const missionNow = mission === null ? null : missionCurrency(mission, historyEntries, currency, snapshot.missionsSeenOpen);
  const pastMission = mission !== null && missionNow !== null && !missionNow.current ? mission : null;
  const shownMission = pastMission === null ? mission : null;
  // A SESSION THAT DATES ITSELF prints every time off today with its date; otherwise the mission's day, as before (5.4).
  const day = currency.today ?? utcDayOf(shownMission?.started_wall_utc ?? null) ?? new Date(nowMs).toISOString().slice(0, 10);

  const robotRead = { status: snapshot.robot, error: snapshot.robotError, readAtMs: snapshot.robotReadAtMs };
  const link = robotLink(robotRead, nowMs);
  const permission = motionPermissionView(robotRead, nowMs);
  const disconnected = snapshot.sourceError !== null;
  const lostAt = [...snapshot.links].reverse().find((item) => item.lost)?.at ?? null;

  const selected = selectedRun(view, snapshot.session.runtime_replaced);
  const runView = viewForSelectedRun(view, selected, snapshot.hydrated);
  // A RUN ON THE RECORD: the snapshot's or the fold's, or a run.started the stream delivered before the snapshot names it.
  const running = runIsActive(view) || streamRunOpen(view);
  // THE RUN ON THE SCREEN IS CURRENT, or there is none (spec section 12): after a restart the runtime's last outcome and
  // its retained events still name an earlier session's run, which is History's.
  const selectedCurrent =
    selected.run_id === null ||
    runCurrency(
      selected.run_id,
      { active: activeRunOnRecord(view) === selected.run_id, history: historyEntries, times: [runView.run_started?.run_id === selected.run_id ? runView.run_started.started_wall_utc : null, runView.outcome.run_id === selected.run_id ? runView.outcome.finished_wall_utc : null] },
      currency,
    ).current;
  const screenRunId = selectedCurrent ? selected.run_id : null;
  const target = timelineTarget(shownMission, screenRunId);

  // EVERY RUN OF THE TARGET, folded once, so the stages span the mission's attempts at it. Reads only.
  const foldKey = target === null ? '' : target.runs.filter((id) => id !== screenRunId).join(',');
  useEffect(() => {
    if (foldKey === '') return;
    void store.foldRuns(foldKey.split(','));
  }, [store, foldKey]);

  const readOnly = replaying || !view.contract_compatible || !view.commands_enabled;
  const readOnlyReason = readOnlyWords(view, replaying, undefined, snapshot.handshake.state !== 'not_answered') ?? '';

  // EVERY COMMAND CONTROL BUT STOP IS OFF WHILE THE RUNTIME IS NOT ANSWERING OR ITS SNAPSHOT IS NOT CURRENT, with the one
  // reason the controls in the panels give too (verdict 2026-09-27 must_fix 3).
  const runtimeOff = runtimeAnswerBlock(snapshot, nowMs);
  const startReading = { mission, missionError: snapshot.missionError, missionErrorDetail: snapshot.missionErrorDetail, runActive: running };
  const startOff = readOnly
    ? readOnlyReason
    : runtimeOff ??
      (snapshot.commandInFlight ? COMMAND_NOT_SETTLED : null) ??
      // THE VLA BEAT drives the same arm from its own process on the robot: Start never competes with it.
      vlaStartBlock(snapshot.vla, snapshot.vlaMaybeDriving) ??
      missionStartOff(startReading) ??
      // A HOLD NOTHING ON THIS PAGE CAN LIFT, in the motion hold's own words, before the capability's reason: that reason
      // may ask for a confirmation the page does not offer.
      (permission.applies && !permission.permitted && permission.confirmOff !== null ? `robot motion is held. Nothing to confirm: ${permission.confirmOff}` : null) ??
      capabilityOffReason(view, 'start_cycle', mission) ??
      robotBlock(view, nowMs) ??
      // THE ROBOT'S MOTION PERMISSION, read as the motion-hold banner reads it: Start is never on beside a hold.
      (permission.applies && !permission.permitted ? `robot motion is held: ${permission.why ?? permission.code ?? 'the robot gave no reason'}` : null) ??
      startRefusal(snapshot.commands);
  // STOP, from the runtime's own capability and the hold's list, over the last snapshot: never off because a read failed,
  // and on from the moment a command is sent until the record says nothing runs (verdict 2026-09-27 must_fix 2).
  const stopOff = stopOffReason(view, stopBusy, mission, commandPending(snapshot));

  // THE START DIALOG CLOSES WHEN THE RECORD SHOWS A RUN (verify-r8 M1): a run another display started, or any run, leaves
  // nothing for its "start the mission" to start, and its scrim must not stand between the operator and Stop. It is not
  // drawn from the render the run shows up in, and the press is forgotten, so it does not come back when the run ends.
  useEffect(() => {
    if (running) setAttesting(false);
  }, [running]);
  // A TAB WHOSE CREDENTIAL THE CONSOLE SERVER NO LONGER ACCEPTS keeps Stop in place as "Reload to stop" (verify-r1 must_fix 1).
  // Its press takes over once when the server refuses the tab's credential as superseded (round 7 item 3); "Reload to
  // stop" is drawn only after a press whose takeover could not send.
  const stopStale = staleStop(snapshot.session, snapshot.lastStop);
  const startBusy = snapshot.startingMission;
  const startLabel = missionStartLabel(startReading, startBusy);

  // THE WRIST THUMBNAIL, watched the same way the classic screen watches it.
  const wristAt = snapshot.telemetry.preview_frame_at_ms_by_role['wrist'] ?? null;
  const wristStream = useStreamDecode(cameraStreamBase(snapshot, 'wrist', store.currentSource, nowMs), wristAt === null ? null : Math.max(0, nowMs - wristAt));
  const wrist = currentWrist(cameraPanel(snapshot, 'wrist', store.currentSource, nowMs, wristStream.decode), currency);

  const checklist = readyChecklist({
    view,
    sourceKind: snapshot.sourceKind,
    sourceError: snapshot.sourceError,
    replaying,
    nowMs,
    referenceDay: day,
    robot: snapshot.robot,
    link,
    permission,
    unresolved: snapshot.liveUnresolved,
    wrist: { showing: wrist.showingVideo, note: wrist.note },
    startOff,
    handshake: snapshot.handshake,
    snapshotRead: snapshot.runtimeAnswered,
  });
  const ready = readiness({
    // THE MISSION ON THIS SCREEN, never History's (spec 12.2, verify-r2 M1): an ended mission's step list is its end's
    // reading, not a reading of now. An open or held mission is always current, so rule 1 still sees it.
    mission: shownMission,
    runActive: running,
    startOff,
    checklist: checklist.items,
    // NO TIME (verify-r1 must_fix 4): this page's first failed read is not the runtime's time, and a reload would move it.
    disconnected: disconnected ? 'The runtime is not answering.' : null,
  });

  const drawnRuns = timelineRuns(snapshot, screenRunId, runView, shownMission);
  const stages = stageTimeline(drawnRuns, shownMission, target);
  // THE SCENE MAP folds named runs' own records, in run order: the scan the operator pressed, then what a cycle saw; never
  // an earlier run's records carried by the live fold, so a reload draws the same Scene and says which runs (item 8).
  const sceneRuns = sceneRecords({
    drawnRuns,
    // ONLY THIS SESSION'S FINISHED RUNS: a scan of an earlier session or day is History's (spec section 12).
    history: historyEntries.filter((entry) => runCurrency(entry.run_id, { active: false, history: historyEntries, times: [] }, currency).current),
    live: view,
    held: (runId) =>
      snapshot.runFolds[runId] ??
      (snapshot.hydrated?.run_id === runId ? snapshot.hydrated.view : null) ??
      (view.recent_events.some((event) => event.kind === 'run.started' && event.run_id === runId) ? view : null),
  });
  const sceneMap = foldSceneMap(sceneRuns.flatMap((run) => run.events));
  const sceneDrawnFrom = sceneRecordsLine(sceneRuns, day);
  const capability = snapshot.robot?.configured === true ? snapshot.robot.capability : null;

  // A SOURCE IS STALE when its last read failed or its last good read is older than 15 s while the snapshot answers
  // (rule 4.1); the marker prints the fixed time of that read and flips once.
  const missionStale = snapshot.missionReadAtMs !== null && (snapshot.missionError !== null || (!disconnected && nowMs - snapshot.missionReadAtMs > ROBOT_ANSWER_STALE_AFTER_MS));
  const robotStale = snapshot.robot !== null && (snapshot.robotError !== null || link.stale);
  const outcome = outcomeCard({
    mission,
    currency: missionNow,
    referenceDay: day,
    missionError: snapshot.missionError,
    history: snapshot.history?.ok === true ? snapshot.history.entries : [],
    runEvents: snapshot.missionRunEvents,
    // THE RUN IN PROGRESS BY THE NEWER RECORD (verify-r1 must_fix 5), the rule the stages' selected run reads.
    activeRunId: activeRunOnRecord(view),
    // ONE ANSWER FOR THE CURRENT RUN (verdict item 6): the card names the run the stages are drawn for.
    runOnScreen: stages.run_ids[stages.run_ids.length - 1] ?? null,
    acknowledgements: snapshot.acknowledgements,
    capability,
    drawnRunEvents: Object.fromEntries(drawnRuns.map((run) => [run.run_id, ownRecords(run.run_id, run.view.recent_events)])),
  });
  const cake = cakeStory({
    mission: shownMission,
    past: pastMission === null || missionNow === null ? null : { mission: pastMission, reason: missionNow.reason },
    referenceDay: day,
    candidates: snapshot.missionCandidates,
    signature: snapshot.signatureCheck,
    runtimeSession: view.runtime_session,
    robot: snapshot.robot,
    capabilityCheck: snapshot.capabilityCheck,
    robotReadAtMs: snapshot.robotReadAtMs,
    missionError: snapshot.missionError,
    signatureCheckError: snapshot.signatureCheckError,
  });
  // THE SLOT THE SCENE NAMES is the footer's: the newest record by time, with its time (browser r2 N1).
  const slot = liveSlot({ robot: snapshot.robot, robotReadAtMs: snapshot.robotReadAtMs, mission: shownMission, day });

  // THE DOCUMENT TITLE says an unacknowledged ending, so a display in another window still carries it (rule 6.2.5).
  const documentTitle = outcome.document_title;
  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.title = documentTitle ?? 'shinro';
  }, [documentTitle]);

  // THE TIMELINE FOLLOWS ITS END until the operator scrolls away; entries that arrive after are counted (rule 6.4).
  // THE TIMELINE WITHOUT HISTORY'S MISSION (spec section 12): the store's list is append-only; the screen filters it.
  // Every mission this page read that is History's, not only the one served now (verify-r1 M1): after Start the runtime
  // serves the new mission, and the earlier one's lines are still in the append-only list.
  const timelineEntries = currentTimelineEntries(snapshot.timeline, pastMissions(snapshot.missionsRead, mission, historyEntries, currency, snapshot.missionsSeenOpen));
  const entryCount = timelineEntries.length;
  useEffect(() => {
    const appended = entryCount - seenEntries.current;
    seenEntries.current = entryCount;
    if (appended <= 0) return;
    setFollowState((state) => follow(state, { kind: 'appended', count: appended }));
    const list = listRef.current;
    if (list !== null && followState.following) list.scrollTop = list.scrollHeight;
  }, [entryCount, followState.following]);
  // A width or layout change rewraps the lines: a following list stays at its end, a paused one stays where it is.
  const followRef = useRef(followState);
  useEffect(() => {
    followRef.current = followState;
  }, [followState]);
  useEffect(() => {
    const list = listRef.current;
    if (list === null || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (followStep(followRef.current, { kind: 'resized' }).scrollToEnd) list.scrollTop = list.scrollHeight;
    });
    observer.observe(list);
    for (const child of Array.from(list.children)) observer.observe(child);
    return () => observer.disconnect();
  }, [entryCount]);
  const onTimelineScroll = (event: UIEvent<HTMLOListElement>): void => {
    const box = event.currentTarget;
    setFollowState((state) => follow(state, { kind: 'scrolled', atEnd: isAtEnd(box) }));
  };
  const onJump = (): void => {
    setFollowState((state) => follow(state, { kind: 'jump' }));
    const list = listRef.current;
    if (list !== null) list.scrollTop = list.scrollHeight;
  };

  const byHand = missionSteps(mission).map(nextStep).filter((step) => step.byHand).map((step) => step.words);
  const startLines = [...ATTESTATIONS, ...byHand];
  // ONE BOX PER LINE: a Start press sets them all unticked; before one (the opening), all as the opening says.
  const boxes = checked.length === startLines.length ? checked : startLines.map(() => opening.attest?.allChecked ?? false);
  const runStart = (confirmed: readonly string[] = []): void => {
    void pressStart(store, confirmed);
  };
  const onStartPressed = (): void => {
    if (view.data_mode !== 'synthetic') {
      setChecked(startLines.map(() => false));
      setAttesting(true);
      return;
    }
    runStart();
  };
  const onStop = (): void => {
    setStopBusy(true);
    // THE ONE STOP STOPS EVERY DRIVER: while the VLA policy may be driving the arm, its runner is told to stop too.
    if (snapshot.vlaMaybeDriving) void store.vlaCommand('stop');
    void store.requestStop().finally(() => setStopBusy(false));
  };
  const onRetry = (): void => {
    void pressRetry(store);
  };
  const onAcknowledge = (): void => {
    if (outcome.ack_key === null || outcome.mission_id === null || outcome.verdict === null) return;
    store.acknowledge({ key: outcome.ack_key, mission_id: outcome.mission_id, run_id: outcome.run?.run_id ?? null, attempt: null, verdict: outcome.verdict.id });
  };

  // THE SIGNATURE CHECK is off while the runtime is not answering, while its own read failed (the table shown is the one
  // before), and while the Cake region carries its "Not read since" marker (spec 4.3, Run signature check: Stale and
  // Disconnected disabled; the owner's MEDIUM item). Each in the words of the read that is not current.
  const signatureOff =
    snapshot.signatureCheck === null
      ? snapshot.signatureCheckError === null
        ? 'the signature check has not been read yet'
        : signatureUnavailableWords(snapshot.signatureCheckError, snapshot.signatureCheckErrorDetail)
      : signatureRunOff({
          state: snapshot.signatureCheck,
          running: snapshot.signatureChecking,
          readOnlyReason: readOnly
            ? readOnlyReason
            : runtimeOff ??
              (snapshot.signatureCheckError === null ? null : `the last read of the signature check failed (${snapshot.signatureCheckError}), and the table shown is the one before it`) ??
              missionReadBlock(snapshot, nowMs) ??
              robotReadBlock(snapshot, nowMs),
        });
  const missionStaleAt = missionStale ? readTime(snapshot.missionReadAtMs, day) : null;
  const vlaBlock = vlaStartBlock(snapshot.vla, snapshot.vlaMaybeDriving);
  const activationOff = readOnly ? readOnlyReason : runtimeOff ?? vlaBlock ?? missionReadBlock(snapshot, nowMs) ?? activationOffReason(mission);
  // A SOURCE WITH NO RETRY DOOR offers no Try again (spec 9: no change to what a control admits).
  const retryOff = readOnly
    ? readOnlyReason
    : runtimeOff !== null
      ? runtimeOff
      : vlaBlock !== null
        ? vlaBlock
        : !store.retryOffered
        ? 'this runtime does not offer a mission retry'
        : retryRefusal(snapshot, nowMs);

  const robotConnection = snapshot.robot === null ? link.headline : link.state === 'answering' ? 'connected' : link.headline;

  return (
    // THE TOP BAR'S HEIGHT, handed to every dialog under this screen: none covers the only Stop (`ui/stopBar.ts`).
    <StopBarHeight value={topBarHeight}>
      <div
        className="dc"
        data-screen="demo"
        style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: SURFACE.page, color: INK.body, fontFamily: 'var(--font-sans), system-ui, sans-serif', WebkitFontSmoothing: 'antialiased' }}
      >
        <TopBar
          mode={modeMark({ dataMode: view.data_mode, sourceKind: snapshot.sourceKind, replaying })}
          robot={{ label: link.label, connection: robotConnection, tone: link.state === 'answering' && !link.stale ? 'ok' : snapshot.robot === null ? 'mute' : 'warn', notReadSince: robotStale ? readTime(snapshot.robotReadAtMs, day) : null }}
          motion={!permission.applies ? null : permission.permitted ? { words: 'Motion permitted', tone: 'ok' } : { words: `Motion held: ${permission.code ?? permission.why ?? 'no reason given'}`, tone: 'bad' }}
          readiness={ready}
          start={{ label: startLabel, off: startOff, busy: startBusy }}
          stop={{ off: stopOff, busy: stopBusy, stale: stopStale }}
          onStart={onStartPressed}
          onStop={onStop}
          onReload={reloadPage}
          onDiagnostics={() => setDrawer('connections')}
          onHeight={setTopBarHeight}
        />

        <div className="console__body" style={{ flex: 'none', padding: '0 20px' }}>
          <StripAlerts snapshot={snapshot} mode={mode} sourceKind={snapshot.sourceKind} />
          <UncertainPanel snapshot={snapshot} store={store} />
          <MotionHold permission={permission} link={link} snapshot={snapshot} store={store} readOnlyReason={readOnly ? readOnlyReason : runtimeOff} />
        </div>

        {store.vlaOffered ? (
          // THE VLA BEAT, above the demo grid and outside it (its four children are pinned), so it is on screen without scrolling:
          // the mission start pose before the mission; the scripted carry once the stopped mission holds the block; the swap once
          // mission 1 is finished (by that carry, or by a mission that placed its block for real); kept while the robot-side
          // runner loads, is ready or runs. The carry reads the mission ON THIS SCREEN: one of an earlier runtime session is
          // History's, and after a restart its record of holding the block is no longer where the arm is. Every press is
          // forwarded; the runner decides.
          <div className="console__vla" style={{ flex: 'none', padding: '0 20px 16px' }}>
            <VlaPanel
              view={vlaView(snapshot.vla, mission !== null && mission.state === 'COMPLETE' && missionExecution(mission) === 'physical', !running && !snapshot.commandInFlight, snapshot.vlaRefusal, missionHold(shownMission))}
              onAction={(action) => void store.vlaCommand(action)}
              busy={snapshot.vlaBusy}
            />
          </div>
        ) : null}

        <main className="demo-grid">
          <div className="demo-grid__mission">
            <OutcomeCard
              model={outcome}
              returnArm={<Actions snapshot={snapshot} store={store} only={['return_arm']} primary={outcome.primary_step === 'return_arm' ? 'return_arm' : null} />}
              retrying={snapshot.retryingMission}
              retryOff={retryOff}
              onRetry={onRetry}
              onAcknowledge={onAcknowledge}
              notReadSince={missionStaleAt}
              dimmed={disconnected}
              frameUrl={(id) => frameAddress(store.currentSource, id)}
              onHistory={() => setDrawer('history')}
            />
          </div>
          <div className="demo-grid__cake">
            <CakeStory
              model={cake}
              activating={snapshot.activating}
              activationOff={activationOff}
              signatureRunOff={signatureOff}
              signatureRunning={snapshot.signatureChecking}
              signatureServed={snapshot.signatureCheck !== null}
              onActivate={(key) => void store.activateCandidate(key)}
              onRunSignature={() => void store.runSignatureCheck()}
              notReadSince={missionStaleAt ?? (robotStale ? readTime(snapshot.robotReadAtMs, day) : null)}
              dimmed={disconnected}
            />
          </div>
          {/* THE LIVE STATUS IN THE MIDDLE, THE TIMELINE BELOW IT (spec 2.1; operator layout ruling 2026-09-28): the same
              places whether or not anything runs, so nothing jumps when a run starts or ends. */}
          <div className="demo-grid__strip" style={{ opacity: disconnected ? 0.6 : 1 }}>
            <StageTimeline timeline={stages} referenceDay={day} />
            {/* ONE HEIGHT BELOW 1100 PX (verify-r5 browser must_fix 1): the strip stacks there, and the scene's caption and
                legend grow once a run is drawn, which moved the wrist camera under it 61 and 46 px. The scene draws its map
                and legend in a bounded region that scrolls within the height design.css gives it; at desktop width the
                scene sits beside the wrist and keeps its natural height. */}
            <section aria-label="Scene" data-region="scene" className="demo-strip__scene">
              <BoundedAccount name="scene" label="The scene">
                <SceneMap map={sceneMap} slot={slot} title="Scene" drawnFrom={sceneDrawnFrom} day={day} />
              </BoundedAccount>
            </section>
            <WristThumb
              panel={wrist}
              binding={wristStream.binding}
              kept={wristStream.kept}
              referenceDay={day}
              dimmed={disconnected}
              controls={<Actions snapshot={snapshot} store={store} only={['scan_scene', 'observe']} primary={null} />}
            />
          </div>
          <div className="demo-grid__timeline">
            <TimelineRegion
              entries={timelineEntries}
              follow={followState}
              onJump={onJump}
              onScroll={onTimelineScroll}
              listRef={listRef}
              notReadSince={disconnected && lostAt !== null && snapshot.runtimeAnswered ? (utcStamp(lostAt, day)?.text ?? null) : null}
              notAnswered={disconnected && !snapshot.runtimeAnswered}
              missionUnread={mission === null && snapshot.missionError !== null && snapshot.missionError !== 'no_mission_recorded' ? snapshot.missionError : null}
            />
          </div>
        </main>

        {attesting && !running ? (
          <Dialog
            title="Before the mission starts"
            onClose={() => setAttesting(false)}
            actions={
              <>
                <button type="button" className="button" onClick={() => setAttesting(false)}>
                  cancel
                </button>
                <button
                  type="button"
                  className="button button--primary"
                  // OFF WHENEVER THE TOP BAR'S START IS OFF, for Start's own reason, shown above: the dialog never offers a
                  // command the runtime would refuse (verify-r8 M1).
                  disabled={startOff !== null || startBusy || !boxes.every((item) => item)}
                  onClick={() => {
                    setAttesting(false);
                    runStart(startLines);
                  }}
                >
                  start the mission
                </button>
              </>
            }
          >
            {startOff === null ? null : (
              <p data-control-reason="attest-start" style={{ color: C.warn }}>
                Start is off. {asSentence(startOff)}
              </p>
            )}
            <StartChecks lines={startLines} checked={boxes} onChange={(index, value) => setChecked(boxes.map((item, at) => (at === index ? value : item)))} />
          </Dialog>
        ) : null}

        {drawer === null ? null : <Drawer id={drawer} snapshot={snapshot} store={store} mode={mode} top={topBarHeight} onSelect={setDrawer} onClose={() => setDrawer(null)} />}
      </div>
    </StopBarHeight>
  );
}
