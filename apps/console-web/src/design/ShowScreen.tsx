// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
/**
 * THE SHOW SCREEN: the recorded Cake demo in four clicks (operator ruling 2026-10-11): Confirm this robot, Start,
 * Enable VLA, Start. It shows the controls, the wrist camera in the middle (the view the policies act on), the front
 * camera, the five stages and Cake's status, and nothing else: no scene map, no run account, no log.
 *
 * Start on mission 1 brings the arm to the mission's start pose, starts the runtime's mission and arms the demo
 * director (model/director.ts), which presses on its own what the record asks for next: the Cake activation, the stop
 * at the hold, the runner's carry. Enable VLA loads the VLA module on the robot; Start then runs the VLA episode.
 */
import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { stopOffReason, streamRunOpen } from '../model/failureBanner';
import { missionIsRunning, missionStartLabel, missionStartOff } from '../model/missionView';
import { cakeStory } from '../model/cakeStory';
import { modeMark } from '../model/modeMark';
import { staleStop } from '../model/reconnect';
import { readiness } from '../model/readiness';
import { readyChecklist } from '../model/readyChecklist';
import { motionPermissionView, robotLink } from '../model/robotStatus';
import { runIsActive } from '../model/steps';
import { activeRunOnRecord, selectedRun, needsHydration, viewForSelectedRun } from '../model/selectedRun';
import { stageTimeline } from '../model/stageTimeline';
import { utcDayOf } from '../model/utcTime';
import { currencyContext, missionCurrency, runCurrency } from '../model/currency';
import { enableVla, mission1Stages, mission2Stages, showPhase, showWords, startRoute, vlaStartOff } from '../model/show';
import { vlaStartBlock } from '../model/vla';
import { keptPictureAtView } from '../model/streamDecode';
import { COMMAND_NOT_SETTLED, commandPending, readOnlyWords, robotBlock, runtimeAnswerBlock, startRefusal } from '../panels/commandGuards';
import { StripAlerts } from '../panels/StatusStrip';
import { UncertainPanel } from '../panels/UncertainPanel';
import { useConsole, useNowMs, useStore } from '../store/useConsole';
import { StopBarHeight } from '../ui/stopBar';
import { Button } from './Button';
import { CakeStory } from './CakeStory';
import { cameraPanel, cameraStreamBase } from './cameras';
import type { CameraPanelModel } from './cameras';
import { currentWrist, timelineRuns, timelineTarget, wristLine, wristWord } from './DemoScreen';
import { Drawer } from './Drawers';
import type { DrawerId } from './Drawers';
import { MotionHold } from './RobotParts';
import { StageTimeline } from './StageTimeline';
import { C, INK, SURFACE, wash } from './theme';
import { TopBar } from './TopBar';
import { useStreamDecode } from './useStreamDecode';
import type { StreamBinding } from './useStreamDecode';
import { asSentence } from './actionCard';

const TONE_COLOUR = { neutral: INK.bright, busy: C.accent, good: C.ok, bad: C.bad } as const;

/** One camera, by role: the stream when it shows, the last decoded picture through a gap, the saved frame otherwise. */
function CameraView(props: { role: 'wrist' | 'front'; label: string; panel: CameraPanelModel; binding: StreamBinding | null; kept: ReturnType<typeof keptPictureAtView> extends infer K ? Exclude<K, null> | null : never; large: boolean; dimmed: boolean }): ReactNode {
  const { panel } = props;
  const kept = props.kept;
  return (
    <section aria-label={props.label} data-region={props.role} data-live-view={panel.role} style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8, opacity: props.dimmed ? 0.6 : 1 }}>
      <div style={{ fontSize: 13, color: INK.quiet }}>{props.label}</div>
      <div style={{ position: 'relative', width: '100%', aspectRatio: '16 / 9', border: '1px solid var(--color-border)', borderRadius: props.large ? 12 : 8, overflow: 'hidden', background: SURFACE.well }}>
        {panel.streamUrl !== null && props.binding !== null ? (
          <img key={props.binding.key} ref={props.binding.ref} onLoad={props.binding.onLoad} onError={props.binding.onError} data-camera-stream="" src={panel.streamUrl} alt={`the ${panel.role} camera stream`}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', opacity: panel.showingVideo ? 1 : 0 }} />
        ) : null}
        {kept === null ? null : <img data-kept-picture="" src={kept.url} alt={`the last picture this page decoded from the ${panel.role} camera`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain', filter: 'saturate(.6)' }} />}
        {kept === null && !panel.showingVideo && panel.stillUrl !== null ? <img data-camera-still="" src={panel.stillUrl} alt={`the newest stored frame from the ${panel.role} camera`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain' }} /> : null}
      </div>
      <div data-camera-chip="" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: panel.tone }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: panel.tone }}>{wristWord(panel)}</span>
        <span data-camera-note="" style={{ fontSize: 12, color: INK.soft, overflowWrap: 'anywhere' }}>{wristLine(panel, kept !== null)}</span>
      </div>
    </section>
  );
}

export function ShowScreen(): ReactNode {
  const store = useStore();
  const snapshot = useConsole();
  const nowMs = useNowMs();
  const [drawer, setDrawer] = useState<DrawerId | null>(null);
  const [topBarHeight, setTopBarHeight] = useState(0);
  const [stopBusy, setStopBusy] = useState(false);

  useEffect(() => {
    void store.start();
    return () => {
      store.dispose();
    };
  }, [store]);

  const view = snapshot.view;
  const replaying = snapshot.replay !== null;
  const mode = replaying ? 'replay' : view.data_mode;
  const mission = snapshot.mission;
  const historyEntries = snapshot.history?.ok === true ? snapshot.history.entries : [];
  const currency = currencyContext(view.runtime_session, nowMs);
  const missionNow = mission === null ? null : missionCurrency(mission, historyEntries, currency, snapshot.missionsSeenOpen);
  const shownMission = mission !== null && missionNow !== null && missionNow.current ? mission : null;
  const day = currency.today ?? utcDayOf(shownMission?.started_wall_utc ?? null) ?? new Date(nowMs).toISOString().slice(0, 10);

  const robotRead = { status: snapshot.robot, error: snapshot.robotError, readAtMs: snapshot.robotReadAtMs };
  const link = robotLink(robotRead, nowMs);
  const permission = motionPermissionView(robotRead, nowMs);
  const disconnected = snapshot.sourceError !== null;

  // THE RUN ON THE RECORD, folded, for mission 1's first two stages; the rest of the rows come from the runner.
  const selected = selectedRun(view, snapshot.session.runtime_replaced);
  const runView = viewForSelectedRun(view, selected, snapshot.hydrated);
  const hydrateId = needsHydration(selected, view, snapshot.hydrated) ? selected.run_id : null;
  useEffect(() => {
    if (hydrateId !== null) void store.hydrateRun(hydrateId);
  }, [store, hydrateId]);
  const running = runIsActive(view) || streamRunOpen(view);
  // THE RUN ON THE SCREEN IS CURRENT, or there is none: after a restart the runtime's last outcome still names an earlier
  // session's run, which is History's (the rule the demo screen keeps); its failed carry is not this session's.
  const selectedCurrent =
    selected.run_id === null ||
    runCurrency(
      selected.run_id,
      { active: activeRunOnRecord(view) === selected.run_id, history: historyEntries, times: [runView.run_started?.run_id === selected.run_id ? runView.run_started.started_wall_utc : null, runView.outcome.run_id === selected.run_id ? runView.outcome.finished_wall_utc : null] },
      currency,
    ).current;
  const screenRunId = selectedCurrent ? selected.run_id : null;
  const target = timelineTarget(shownMission, screenRunId);
  const foldKey = target === null ? '' : target.runs.filter((id) => id !== screenRunId).join(',');
  useEffect(() => {
    if (foldKey !== '') void store.foldRuns(foldKey.split(','));
  }, [store, foldKey]);
  const controller = stageTimeline(timelineRuns(snapshot, screenRunId, runView, shownMission), shownMission, target).rows;

  const vla = snapshot.vla;
  const phase = showPhase(vla);
  const rows = phase === 'mission2' ? mission2Stages(vla) : mission1Stages(controller, shownMission, vla);
  const missionRunning = mission !== null && missionIsRunning(mission);
  const runtimeIdle = !running && !snapshot.commandInFlight && !missionRunning;
  const words = showWords({ phase, mission: shownMission, vla, step: snapshot.demoArmed === null ? null : snapshot.directorStep, runtimeIdle, vlaRefusal: snapshot.vlaRefusal });

  const readOnly = replaying || !view.contract_compatible || !view.commands_enabled;
  const readOnlyReason = readOnlyWords(view, replaying, undefined, snapshot.handshake.state !== 'not_answered') ?? '';
  const runtimeOff = runtimeAnswerBlock(snapshot, nowMs);
  const startReading = { mission, missionError: snapshot.missionError, missionErrorDetail: snapshot.missionErrorDetail, runActive: running };
  const route = startRoute(vla);
  const startOff = readOnly
    ? readOnlyReason
    : runtimeOff ??
      (commandPending(snapshot) || snapshot.demoStarting ? COMMAND_NOT_SETTLED : null) ??
      (route === 'vla'
        ? vlaStartOff(vla, runtimeIdle, snapshot.vlaBusy)
        : vlaStartBlock(vla, snapshot.vlaMaybeDriving) ??
          missionStartOff(startReading) ??
          (permission.applies && !permission.permitted && permission.confirmOff !== null ? `robot motion is held. Nothing to confirm: ${permission.confirmOff}` : null) ??
          robotBlock(view, nowMs) ??
          (permission.applies && !permission.permitted ? `robot motion is held: ${permission.why ?? permission.code ?? 'the robot gave no reason'}` : null) ??
          startRefusal(snapshot.commands));
  const startBusy = snapshot.startingMission || snapshot.demoStarting || (route === 'vla' && snapshot.vlaBusy);
  const startLabel = route === 'vla' ? (vla?.state === 'running' ? 'Running' : 'Start') : missionStartLabel(startReading, startBusy);
  // A DEMO START IN FLIGHT MAY BE MOVING THE ARM before the runner is read moving and before any run exists: Stop is on.
  const stopOff = snapshot.vlaMaybeDriving || snapshot.demoStarting ? null : stopOffReason(view, stopBusy, mission, commandPending(snapshot));
  const stopStale = staleStop(snapshot.session, snapshot.lastStop);

  // THE WRIST VIEW feeds the top bar's checklist as before; the front view is the picture in the middle.
  const frameAge = (role: 'wrist' | 'front'): number | null => {
    const at = snapshot.telemetry.preview_frame_at_ms_by_role[role] ?? null;
    return at === null ? null : Math.max(0, nowMs - at);
  };
  const wristStream = useStreamDecode(cameraStreamBase(snapshot, 'wrist', store.currentSource, nowMs), frameAge('wrist'));
  const frontStream = useStreamDecode(cameraStreamBase(snapshot, 'front', store.currentSource, nowMs), frameAge('front'));
  const wrist = currentWrist(cameraPanel(snapshot, 'wrist', store.currentSource, nowMs, wristStream.decode), currency);
  const front = currentWrist(cameraPanel(snapshot, 'front', store.currentSource, nowMs, frontStream.decode), currency);
  const checklist = readyChecklist({
    view, sourceKind: snapshot.sourceKind, sourceError: snapshot.sourceError, replaying, nowMs, referenceDay: day, robot: snapshot.robot, link, permission,
    unresolved: snapshot.liveUnresolved, wrist: { showing: wrist.showingVideo, note: wrist.note }, startOff, handshake: snapshot.handshake, snapshotRead: snapshot.runtimeAnswered,
  });
  const vlaDriving = vla?.state === 'running' || vla?.state === 'loading' || vla?.state === 'moving';
  const ready = readiness({ mission: shownMission, runActive: running || vlaDriving, startOff, checklist: checklist.items, disconnected: disconnected ? 'The runtime is not answering.' : null });

  const enable = enableVla({ vla, missionRunning, runActive: running, vlaBusy: snapshot.vlaBusy });
  const cake = cakeStory({
    mission: shownMission, past: null, referenceDay: day, candidates: snapshot.missionCandidates, signature: snapshot.signatureCheck,
    runtimeSession: view.runtime_session, robot: snapshot.robot, capabilityCheck: snapshot.capabilityCheck, robotReadAtMs: snapshot.robotReadAtMs, missionError: snapshot.missionError,
    signatureCheckError: snapshot.signatureCheckError,
  });
  // THE DIRECTOR PRESSES THE PLAN: the candidate buttons stay off the screen while a mission is directed here.
  const cakeShown = snapshot.demoArmed === null ? cake : { ...cake, pending: null };

  const onStart = (): void => {
    if (route === 'vla') void store.vlaCommand('run');
    else void store.startDemo();
  };
  const onStop = (): void => {
    setStopBusy(true);
    void store.stopDemo().finally(() => setStopBusy(false));
  };

  return (
    <StopBarHeight value={topBarHeight}>
      <div className="dc" data-screen="show" style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: SURFACE.page, color: INK.body, fontFamily: 'var(--font-sans), system-ui, sans-serif', WebkitFontSmoothing: 'antialiased' }}>
        <TopBar
          mode={modeMark({ dataMode: view.data_mode, sourceKind: snapshot.sourceKind, replaying })}
          robot={{ label: link.label, connection: link.state === 'answering' ? 'connected' : link.state, tone: link.state === 'answering' && !link.stale ? 'ok' : snapshot.robot === null ? 'mute' : 'warn', notReadSince: null }}
          motion={!permission.applies ? null : permission.permitted ? { words: 'Motion permitted', tone: 'ok' } : { words: `Motion held: ${permission.code ?? permission.why ?? 'no reason given'}`, tone: 'bad' }}
          readiness={ready}
          start={{ label: startLabel, off: startOff, busy: startBusy }}
          stop={{ off: stopOff, busy: stopBusy, stale: stopStale }}
          onStart={onStart}
          onStop={onStop}
          onReload={() => window.location.reload()}
          onDiagnostics={() => setDrawer('connections')}
          onHeight={setTopBarHeight}
          extra={
            store.vlaOffered ? (
              <Button variant={enable.done ? 'outline' : 'secondary'} data-control="enable-vla" disabled={enable.off !== null} aria-disabled={enable.off !== null ? 'true' : 'false'}
                title={enable.off === null ? 'Enable the VLA module on the robot: it loads the policy and brings the arm to its start pose.' : asSentence(enable.off)} onClick={() => void store.vlaCommand('swap')}>
                {enable.label}
              </Button>
            ) : null
          }
        />

        <div className="console__body" style={{ flex: 'none', padding: '0 20px' }}>
          <StripAlerts snapshot={snapshot} mode={mode} sourceKind={snapshot.sourceKind} />
          <UncertainPanel snapshot={snapshot} store={store} />
          <MotionHold permission={permission} link={link} snapshot={snapshot} store={store} readOnlyReason={readOnly ? readOnlyReason : runtimeOff} />
        </div>

        <main className="show-grid">
          <div className="show-grid__mission" style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <section aria-label="Mission" data-region="mission" data-show-tone={words.tone} style={{ padding: '16px 18px', borderRadius: 12, background: wash(TONE_COLOUR[words.tone], 0.06), borderLeft: `4px solid ${TONE_COLOUR[words.tone]}`, border: '1px solid var(--color-border)', borderLeftWidth: 4, borderLeftColor: TONE_COLOUR[words.tone] }}>
              <div style={{ fontSize: 13, color: INK.quiet }}>Mission</div>
              <div data-show-title="" style={{ fontSize: 22, fontWeight: 650, lineHeight: 1.2, color: INK.bright, marginTop: 4 }}>{words.title}</div>
              <div data-show-status="" style={{ fontSize: 14, color: INK.soft, lineHeight: 1.45, marginTop: 6 }}>{words.status}</div>
            </section>
            <StageTimeline timeline={{ rows, run_ids: [], basis: phase === 'mission2' ? "the robot runner's episode events" : "the runtime's mission record and the runner's carry events" }} referenceDay={day} />
          </div>
          <div className="show-grid__centre" style={{ minWidth: 0 }}>
            <CameraView role="wrist" label="Wrist camera" panel={wrist} binding={wristStream.binding} kept={keptPictureAtView(wrist.showingVideo, wristStream.kept, day)} large dimmed={disconnected} />
          </div>
          <div className="show-grid__side" style={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <CameraView role="front" label="Front camera" panel={front} binding={frontStream.binding} kept={keptPictureAtView(front.showingVideo, frontStream.kept, day)} large={false} dimmed={disconnected} />
            <div style={{ height: 320, minWidth: 0 }}>
              <CakeStory model={cakeShown} activating={snapshot.activating} activationOff={readOnly ? readOnlyReason : runtimeOff} signatureRunOff={readOnly ? readOnlyReason : runtimeOff} signatureRunning={snapshot.signatureChecking}
                signatureServed={snapshot.signatureCheck !== null} onActivate={(key) => void store.activateCandidate(key)} onRunSignature={() => void store.runSignatureCheck()} notReadSince={null} dimmed={disconnected} />
            </div>
          </div>
        </main>

        {drawer === null ? null : <Drawer id={drawer} snapshot={snapshot} store={store} mode={mode} top={topBarHeight} onSelect={setDrawer} onClose={() => setDrawer(null)} />}
      </div>
    </StopBarHeight>
  );
}
