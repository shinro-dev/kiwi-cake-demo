// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ONE WORKSPACE: about two thirds camera, one third what is happening beside it.
//
// The camera stays on the screen through Observe, Run and Result, because watching the robot IS the demonstration.
// What used to sit around it (a second large camera panel that was usually empty, a pad map with nothing on it, a
// six-row phase table, a known-limits catalogue and three overlapping status summaries) is gone; what is left is the
// picture, one phrase for what the controller is doing, the measurements, and which software is actually executing.
//
// THREE RULES THIS FILE EXISTS TO KEEP:
//
//   A STILL IS NEVER CALLED VIDEO. With this baseline the controller writes intermittent stills and no preview source
//   is attached, so the picture is labelled "Latest observation" with its age. The words "live video" appear only for
//   a slot the runtime itself reports as streamable.
//   AN OVERLAY BELONGS TO ITS OWN FRAME. A perception is drawn only over the frame it was computed from; a newer
//   frame never inherits an older decision's boxes. `overlayForFrame` is what enforces it and it is asked every time.
//   A MEASUREMENT IS A MEASUREMENT. Nothing here substitutes a commanded value, an example reading or a zero for one
//   that was not reported. Where a whole group is unavailable it is said once, not field by field.

import type { ReactNode } from 'react';
import { useState } from 'react';
import type { Component } from '@kiwi/demo-contracts';
import { acceptanceNote, resultPredicates } from '../model/acceptance';
import { CameraRoles } from './CameraRoles';
import { cameraReading, proxiedMediaUrl } from '../model/camera';
import { millis, number, textOr, UNAVAILABLE } from '../model/format';
import { overlayLayers } from '../model/overlay';
import { previewView, tapView } from '../model/sources';
import { previewReading, stillReading } from '../model/still';
import {
  ageFromWallUtc,
  FRAME_FRESHNESS_LABEL,
  MEASUREMENT_TITLE,
  measurementReading,
  SOURCE_MEASUREMENT_LABEL,
  TRANSPORT_ACTIVITY_LABEL,
  AGE_MEANING,
} from '../model/measurement';
import { UNSCOPED_COMMAND } from '../model/command';
import type { CommandScope } from '../model/command';
import { currentAction, previousRun, resultForCurrentRun, TELEMETRY_SILENT_AFTER_MS, telemetryAgeMs, telemetryIsCurrent } from '../model/steps';
import { ageWords as elapsedWords } from '../ui/Age';
import { utcStamp } from '../model/utcTime';
import type { ConsoleSource } from '../api/types';
import type { StoreSnapshot } from '../store/store';
import { Badge } from '../ui/Badge';
import { FrameView } from './FrameView';
import type { ScreenMode } from './StatusStrip';

/** Which picture the stage is showing. Only the ones that actually have something behind them are ever offered. */
export type StageView = 'camera' | 'decision' | 'pad';

export interface StageChoice {
  id: StageView;
  label: string;
}

/** The views there is something to show for, in the order they are offered. Never a choice that leads to an empty panel. */
export function stageChoices(snapshot: StoreSnapshot): StageChoice[] {
  const choices: StageChoice[] = [{ id: 'camera', label: 'Camera' }];
  if (snapshot.view.decision_frame?.primary_observation_id != null) choices.push({ id: 'decision', label: 'Decision frame' });
  // The pad map is offered only when perception actually resolved a position on the pad. An empty pad drawn by
  // default is what this screen used to open with, and it said nothing at all.
  if (snapshot.sceneMarks.length > 0) choices.push({ id: 'pad', label: 'Pad map' });
  return choices;
}

/**
 * THE PICTURE.
 *
 * One element is ever pointed at the preview stream, and only when the runtime reports a streamable slot for a named
 * role. Everything else is a STORED frame fetched by observation id, which costs the camera nothing.
 */
export function CameraStage(props: {
  snapshot: StoreSnapshot;
  mode: ScreenMode;
  source: ConsoleSource;
  nowMs: number;
  /** The decision frame's own observation id, when the operator asked to see the exact frame a decision was taken from. */
  showDecisionFrame: boolean;
}): ReactNode {
  const { view, telemetry, frame } = props.snapshot;
  const silentForMs = telemetryAgeMs(telemetry, props.nowMs);
  const camera = previewView(telemetry.preview, telemetry.preview_received_at_ms, props.nowMs);
  const reading = cameraReading({
    camera,
    mode: props.mode,
    sourceKind: props.source.kind,
    silentForMs,
    silentAfterMs: TELEMETRY_SILENT_AFTER_MS,
  });
  const stream = proxiedMediaUrl(reading.stream && reading.role !== null ? props.source.previewUrl(reading.role) : null);

  /**
   * The observation whose picture this browser asked for and did not get. A frame that is named by a record and whose
   * image does not arrive is a DIFFERENT failure from a capture that produced nothing, and until this was carried the
   * card could only show an element with nothing in it under a caption naming a stored frame.
   */
  const [imageFailedFor, setImageFailedFor] = useState<string | null>(null);

  // WHICH STORED FRAME, AND WHAT TO SAY WHEN THERE IS NONE. The fold comes first and the snapshot is the fallback
  // under it, which is what gives a tab opened or reloaded after a capture a frame to draw at all. All three of the
  // sentences the operator asked to be told apart are decided in `model/still.ts` and in no other place.
  const still = stillReading({
    observations: view.observations,
    snapshot_observation: view.snapshot_observation,
    evidence_gaps: view.evidence_gap_events,
    image_failed_for: imageFailedFor,
  });
  const newest = still.still;

  // THE DECISION FRAME, when it was asked for: the exact observation a predicate or an action consumed, with the
  // perception that was computed FROM IT and from nothing newer.
  const decisionId = view.decision_frame?.primary_observation_id ?? null;
  const showing = props.showDecisionFrame && decisionId !== null ? decisionId : newest?.observation_id ?? null;
  // The fold's own record for the frame being shown, or the snapshot's when this tab folded none. Reading only the
  // fold here is what left a snapshot-supplied frame with no pixel size and no caption: the picture would load and
  // nothing on the card would say what it was.
  const meta =
    showing === null ? null : view.observations[showing] ?? (newest !== null && newest.observation_id === showing ? newest : null);
  const px = meta?.width_px == null || meta.height_px == null ? null : { width: meta.width_px, height: meta.height_px };
  // A perception is handed to the view ONLY when the record it came from names the frame being drawn.
  const perception = props.showDecisionFrame && frame.observation_id === showing ? frame.perception : null;
  const imageUrl = showing === null ? null : props.source.frameUrl(showing, 'raw', 960);

  const live = reading.stream && stream.url !== null;
  // FRAME FRESHNESS, from a real timestamp. "12 records ago" is a true statement about a log and not an answer to the
  // question an operator is asking, which is how long ago the picture was taken. The record carries the wall time the
  // runtime wrote it at, so that is what the age is counted from, and it is named as the RECORDING time: the shutter
  // instant is another clock on another computer and nothing on this path relates the two.
  const frameAge = meta === null ? null : frameFreshness(meta.t_wall_utc, meta.seq, view.seq_cursor, props.nowMs);
  // THE VIDEO STATE, SAID SEPARATELY. No live video source is attached on this baseline and that is the ordinary
  // state, not a fault and not a statement about the still: the two were one sentence, and that sentence told an
  // operator there was no picture while a picture was on disk and being served.
  const preview = previewReading(reading, still.state === 'shown');

  return (
    <section className="stage">
      <header className="stage__head">
        <h2 className="stage__title">{live ? 'Live video' : still.state === 'shown' ? 'Latest observation' : still.words}</h2>
        <div className="stage__badges">
          <Badge tone="quiet" code title="a camera role, never a device id">
            {reading.role ?? meta?.source_role ?? 'no camera role stated'}
          </Badge>
          {live ? null : <Badge tone="quiet">still, not video</Badge>}
          {live ? null : <Badge tone="quiet">{preview.words}</Badge>}
          {/* WHICH OF THE TWO SOURCES named this frame. A tab that folded the capture and one that was told about it
              by the snapshot are both correct and are not the same thing, and an operator comparing two screens is
              owed the difference rather than left to guess it. */}
          {still.from === 'snapshot' ? <Badge tone="quiet">named by the runtime snapshot</Badge> : null}
          {props.showDecisionFrame ? <Badge tone="info">the frame this decision was taken from</Badge> : null}
        </div>
      </header>

      {live ? (
        <div className="frame stage__frame">
          {/* Not keyed by anything that changes: this is a stream, and replacing the element restarts it. */}
          <img className="frame__image" src={stream.url ?? ''} alt={`the ${reading.role ?? 'camera'} preview stream`} />
          {props.mode === 'live' ? null : <div className="frame__watermark">{props.mode ?? 'mode not recorded'}</div>}
          <div className="frame__caption">
            <span>received by the runtime {millis(camera.receive_age_ms)} ago</span>
            <span>{number(camera.measured_fps, 1, 'fps')}</span>
          </div>
        </div>
      ) : (
        <FrameView
          imageUrl={imageUrl}
          frameId={showing}
          widthPx={px?.width ?? null}
          heightPx={px?.height ?? null}
          perception={perception}
          overlay={overlayLayers(perception, px, showing)}
          watermark={props.mode === 'live' ? null : props.mode}
          onImageError={setImageFailedFor}
          emptyNote={
            <>
              {/* NEVER ONE SENTENCE FOR THREE THINGS. This used to read "Waiting for image. The controller has stored
                  no frame yet." whatever was true, and on hardware it said exactly that while the frame was on disk
                  and the API was answering for it. The heading is the state's own words and the sentence under it is
                  the state's own sentence; the synthetic script keeps its own, because a page carrying no camera
                  bytes has nothing to do with a controller that captured or failed to capture anything. */}
              <strong>{props.source.kind === 'synthetic' ? 'No camera bytes in this page.' : `${still.words}.`}</strong>
              <span>
                {props.source.kind === 'synthetic'
                  ? 'The synthetic source carries no camera bytes. The scene drawn here is a placeholder, not a picture of anything.'
                  : still.sentence}
              </span>
            </>
          }
          caption={
            meta === null ? null : (
              <>
                <span className="frame__caption-lead">still, not video</span>
                <span>observation {meta.observation_id}</span>
                <span>{meta.source_role} camera</span>
                <span title={AGE_MEANING[FRAME_FRESHNESS_LABEL]}>
                  {FRAME_FRESHNESS_LABEL}: {frameAge?.words ?? UNAVAILABLE}
                </span>
                {frameAge?.detail === undefined || frameAge.detail === null ? null : <span>{frameAge.detail}</span>}
              </>
            )
          }
        />
      )}

      {/* The video state, in its own line, under the picture rather than folded into the words above it. */}
      <p className="stage__preview">{preview.sentence}</p>

      {/* EVERY camera, each answering for itself, so two feeds are visible at once and a stalled one is named on its
          own rather than hidden behind the large picture. */}
      <CameraRoles snapshot={props.snapshot} source={props.source} nowMs={props.nowMs} />
    </section>
  );
}

/**
 * HOW OLD THE PICTURE IS, from a real timestamp.
 *
 * What stood here counted RECORDS: "12 records ago". That is a true statement about the ordered log and it is not an
 * answer to the question somebody standing at the robot is asking, which is how long ago. The observation record
 * carries `t_wall_utc`, the wall time the RUNTIME WROTE THE RECORD at, so that is what the elapsed time is counted
 * from, across this browser's clock and the runtime's.
 *
 * IT IS THE RECORDING TIME AND IT IS NAMED AS ONE. The instant the shutter opened is on the camera's own clock, on
 * another computer, and relating the two needs a clock relation this baseline does not supply. So a capture age
 * proper is not available here and this never claims to be one.
 *
 * The record count stays as a SECONDARY detail, because it earns its place in one case: it is the one quantity both
 * ends agree on exactly, and it is what an operator reads a log with. It is never the primary age again, and for a
 * record with no wall time it is all there is, which the words say plainly.
 */
export function frameFreshness(
  wallUtc: string | null,
  stillSeq: number,
  cursorSeq: number,
  nowMs: number,
): { words: string; detail: string | null } {
  const behind = cursorSeq - stillSeq;
  const position = behind <= 0 ? 'the newest record in this fold' : `${String(behind)} record(s) back in the log`;
  const ageMs = ageFromWallUtc(wallUtc, nowMs);
  if (ageMs === null) {
    return { words: 'the record carries no time, so its age is unavailable', detail: position };
  }
  return { words: `recorded ${elapsedWords(ageMs)} ago`, detail: `recording time ${textOr(wallUtc)}, ${position}` };
}

/** ONE PHRASE for what the controller is doing, with the controller's own state name beside it. */
export function NowPanel(props: { snapshot: StoreSnapshot; ready: boolean; scope?: CommandScope }): ReactNode {
  const action = currentAction(props.snapshot.view, props.ready, props.scope ?? UNSCOPED_COMMAND);
  return (
    <section className="rail__card">
      <h3 className="rail__title">Now</h3>
      <p className="now__phrase">{action.phrase}</p>
      {action.detail === null ? null : <p className="now__detail">{action.detail}</p>}
      {action.state === null ? null : <p className="now__state">controller state {action.state}</p>}
    </section>
  );
}

/**
 * WHAT WAS MEASURED, AND BY WHAT. TWO SOURCES, TWO SECTIONS, NEVER ONE SENTENCE.
 *
 * This panel used to say one thing under one heading: "The host is not reporting measurements: tap_not_configured".
 * That was true about the continuous telemetry tap and false as a statement about measurement, and it was on the
 * screen while the runtime held a controller measurement of the whole pose from the Observe that had just run. Two
 * different facts had been printed as one, and the one that had a measurement in it was the one thrown away.
 *
 *   LAST CONTROLLER MEASUREMENT  one `measured.state` record, written when a controller read the robot. It has a
 *                                recording time and a source and it is NOT a feed; nothing about it refreshes.
 *   CONTINUOUS TELEMETRY         the tap. Genuinely absent on this baseline and deferred, reported in the runtime's
 *                                own words, and never allowed to stand for the measurement above it.
 *
 * Nothing here substitutes a commanded value, an example reading or a zero for one that was not reported. Where a
 * whole group is unavailable it is said once, not field by field.
 */
export function TelemetryPanel(props: { snapshot: StoreSnapshot; nowMs: number; day?: string | null }): ReactNode {
  const { telemetry, view } = props.snapshot;
  const tap = tapView(telemetry.tap, telemetry.tap_received_at_ms, props.nowMs, TELEMETRY_SILENT_AFTER_MS);
  const transportAgeMs = telemetryAgeMs(telemetry, props.nowMs);
  const current = telemetryIsCurrent(transportAgeMs);
  const measurement = measurementReading(view, props.nowMs, props.snapshot.replay !== null || props.snapshot.runtimeAnswered);
  const pose = measurement.state;
  // FIXED TIMES, NEVER AGES (spec 5.4, 6.8): this panel is drawn in Diagnostics, where nothing counts against this
  // display's clock. The measurement is dated by its record; the tap and the stream by when a line reached this page.
  const day = props.day ?? null;
  const recordedAt = utcStamp(pose?.t_wall_utc ?? null, day);
  // THE TIME EACH FEED'S CURRENT RUN OF ARRIVALS BEGAN, never its newest sample's (browser r2 N4, verifier R-2).
  const tapSince = readStamp(telemetry.tap_since_ms ?? null, day);
  const lastLineMs = transportAgeMs === null ? null : Math.max(telemetry.preview_received_at_ms ?? 0, telemetry.tap_received_at_ms ?? 0);

  return (
    <section className="rail__card">
      <h3 className="rail__title">
        {MEASUREMENT_TITLE}{' '}
        <span className="rail__freshness" title={AGE_MEANING[SOURCE_MEASUREMENT_LABEL]} data-t={recordedAt?.iso}>
          {SOURCE_MEASUREMENT_LABEL}: {recordedAt === null ? 'the record carries no time' : `recorded at ${recordedAt.text}`}
        </span>
      </h3>
      {pose === null ? (
        <p className="rail__absent">{measurement.sentence}</p>
      ) : (
        <>
          <dl className="readings">
            {Object.entries(pose.joints).map(([name, value]) => (
              <div className="readings__row" key={name}>
                <dt>{name.replace(/_/g, ' ')}</dt>
                <dd>{value === null ? UNAVAILABLE : number(value, 3)}</dd>
              </div>
            ))}
          </dl>
          <p className="rail__line">
            base {number(pose.base_velocity.x_mps, 3, 'm/s')} x, {number(pose.base_velocity.y_mps, 3, 'm/s')} y,{' '}
            {number(pose.base_velocity.theta_dps, 2, 'deg/s')}
          </p>
          <p className="rail__note">Joint values as the controller recorded them. The record names no unit for them, so none is printed.</p>
          {/* WHERE IT CAME FROM, and what it is not. An operator comparing two tabs is owed the difference between a
              record this one folded and one the runtime named on a snapshot, and nobody may read one record as a feed. */}
          <p className="rail__note">{measurement.sentence}</p>
          <p className="rail__note">
            read from host observation {textOr(pose.consumed_source_stamp.session)} seq {String(pose.consumed_source_stamp.seq)}
            {pose.t_wall_utc === null ? '' : `, recorded at ${pose.t_wall_utc}`}
          </p>
        </>
      )}

      {/* THE TAP, AS ITSELF. It is a different question with a different answer, and it never stands for the one above. */}
      <h3 className="rail__title">Continuous telemetry</h3>
      {/* THE TAP WHEN IT IS ARRIVING, said as that. This line used to read "No continuous telemetry is being received:
          connected" over a tap that was connected, which is the one sentence the tap's own state contradicts. */}
      {tap.live ? (
        <p className="rail__line" data-t={tapSince?.iso}>
          Continuous telemetry is arriving{tap.measured_rate_hz === null ? ', its rate not measured' : ` at ${tap.measured_rate_hz.toFixed(1)} Hz, as the runtime measures it`}
          {tapSince === null ? '' : `, since ${tapSince.text}`}. It is the tap and not the recorded pose above.
        </p>
      ) : (
        <p className="rail__absent">
          No continuous telemetry is being received: {textOr(tap.reason ?? tap.server_state)}. That is a statement about the tap and about nothing else;
          it is not a statement that nothing was measured.
        </p>
      )}
      <p className="rail__note" title={AGE_MEANING[TRANSPORT_ACTIVITY_LABEL]} data-t={readStamp(current ? (telemetry.line_since_ms ?? null) : lastLineMs, day)?.iso}>
        {TRANSPORT_ACTIVITY_LABEL}: {transportWords(lastLineMs, current, day, telemetry.line_since_ms ?? null)}
      </p>
    </section>
  );
}

/**
 * TRANSPORT ACTIVITY IN WORDS: when a line last arrived on the event stream, measured on this computer's clock.
 *
 * It is about the CONNECTION and it says nothing about the robot, which is the whole reason it carries its own label.
 * The screen showed this number beside a readout saying lines were arriving and it read as a contradiction, because
 * two different measurements were sharing one word.
 */
export function transportWords(lastLineMs: number | null, current: boolean, day: string | null, sinceMs: number | null = null): string {
  const at = readStamp(lastLineMs, day);
  if (at === null) return 'no stream line has arrived in this tab';
  if (!current) return `no stream line since ${at.text}`;
  // ARRIVING, said with the time the run of lines began: the newest line's time moved every second (browser r2 N4).
  const since = readStamp(sinceMs, day);
  return since === null ? 'stream lines arriving' : `stream lines arriving since ${since.text}`;
}

/** A time this page read something, stamped as the screen prints a recorded time (spec 5.4). */
function readStamp(ms: number | null, day: string | null): ReturnType<typeof utcStamp> {
  return ms === null ? null : utcStamp(new Date(ms).toISOString(), day);
}

/**
 * WHICH SOFTWARE IS EXECUTING, from the runtime's own component list.
 *
 * `active` and `health` are separate facts and stay separate. A component that is installed and reachable is not a
 * component that is executing this task, and this list never implies it: the word beside each one is the runtime's
 * own, and the evidence sentence under it is the runtime's own too.
 */
export function ModulesPanel(props: { snapshot: StoreSnapshot }): ReactNode {
  const components = props.snapshot.view.components;
  const cake = props.snapshot.view.cake;
  return (
    <section className="rail__card">
      <h3 className="rail__title">Software</h3>
      {components.length === 0 ? (
        <p className="rail__absent">The runtime has not reported its components.</p>
      ) : (
        <ul className="modules">
          {components.map((component) => (
            <li className="modules__item" key={component.id}>
              <span className="modules__name">{component.role}</span>
              <span className={`modules__state modules__state--${component.health}`}>{activityWords(component)}</span>
              <span className="modules__evidence">{component.evidence}</span>
            </li>
          ))}
        </ul>
      )}
      {/* Cake, stated ONCE. The ladder, the slot table, the capability catalogue and the paragraph are in Diagnostics. */}
      <p className="rail__line">Cake: {cake.ladder.mission_participation.value === true ? 'participating in this task' : 'off, and not part of this task'}.</p>
    </section>
  );
}

function activityWords(component: Component): string {
  if (component.active === true) return `executing, ${component.health}`;
  if (component.active === false) return `not executing, ${component.health}`;
  return `the runtime does not say whether it is executing, ${component.health}`;
}

/**
 * THE RESULT OF THE RUN ON THE SCREEN, and never of another one.
 *
 * `resultForCurrentRun` answers by run id. A previous run may be drawn beside it, labelled as previous and with the
 * time it finished, and it supplies nothing to the row above it.
 */
export function ResultPanel(props: { snapshot: StoreSnapshot; scope?: CommandScope }): ReactNode {
  const view = props.snapshot.view;
  const result = resultForCurrentRun(view);
  const previous = previousRun(view);
  // A caller that names no command gets the strictest scope there is, which is the reading this panel had before
  // command types were read at all: the placement outcome, its predicates and nothing relaxed.
  const scope = props.scope ?? UNSCOPED_COMMAND;
  // The records this tab folded live when it has them, and otherwise the ones read back for THIS run, so that a tab
  // which watched the run end, one that opened late and one that reloaded all show the same figures.
  const resultNote =
    result === null ? null : acceptanceNote(result, resultPredicates(view.predicates, result.run_id, props.snapshot.resultDetail));
  return (
    <section className="rail__card">
      <h3 className="rail__title">Result</h3>
      {result !== null && !scope.requires_placement ? (
        /* A COMMAND WITH NO PLACEMENT IN IT REPORTS WHAT IT DID, NOT WHAT A CYCLE WOULD HAVE DONE. The outcome word
           for such a command is `unknown` because there was no placement for the runtime to name an outcome after,
           and printing that word alone under the heading Result is how a successful Observe came to read as a
           failure. The command type is on the record; this says what it says. */
        <>
          <p className="result__outcome result__outcome--plain">
            {scope.kind === 'observe' ? 'Observation captured' : scope.kind === 'return_arm' ? 'Arm returned' : 'Command finished'}
          </p>
          <p className="rail__note">run {textOr(result.run_id)}</p>
          <p className="rail__note">
            This was an {scope.label}, not a cycle. It has nothing to grasp and nothing to place, so the four placement checks were not evaluated and
            their absence is the record being correct. The cycle has not been run.
          </p>
        </>
      ) : result === null ? (
        <p className="rail__absent">
          {props.snapshot.replay === null && !props.snapshot.runtimeAnswered
            ? // NOTHING WAS READ (verify-r1 must_fix 3): the empty record's sentence would be a claim about records never read.
              `Not read: the runtime has not answered this display${props.snapshot.sourceError === null ? '' : ` (${props.snapshot.sourceError})`}, so whether a run has finished is not known here.`
            : view.active_run === null
              ? 'No run has finished in this runtime session.'
              : 'The run on the screen has not reported a result yet.'}
        </p>
      ) : (
        <>
          <p className={`result__outcome result__outcome--${result.confirmed === null ? 'plain' : 'confirmed'}`}>{textOr(result.reported)}</p>
          {result.reason_code === null ? null : <p className="rail__line">why: {result.reason_code}</p>}
          <p className="rail__note">run {textOr(result.run_id)}</p>
          {/* A placement that missed a target is described by what it DID and by the measurement it missed, so that a
              run which placed the cube is never read as a run nobody can account for. */}
          {resultNote === null ? null : <p className="rail__note">{resultNote}</p>}
        </>
      )}
      {previous === null ? null : (
        <p className="rail__previous">
          Previous run: {textOr(previous.reported)}
          {previous.finished_wall_utc === null ? '' : `, finished ${previous.finished_wall_utc}`}. It is not this run&apos;s result.
        </p>
      )}
    </section>
  );
}
