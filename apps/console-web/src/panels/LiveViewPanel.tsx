// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// What the robot sees, now. This panel is kept strictly apart from the decision frame: it moves, and nothing on it is
// evidence for any result.
//
// ONE CAMERA OWNER. Exactly one element on this screen is ever pointed at the preview stream, for the one role the
// runtime publishes. Everything else that shows a picture, here and in the decision panel, is a STORED frame fetched
// through the API by its observation id, which costs the camera nothing. That is why adding a view to this screen
// never adds a consumer of the camera: a second stream would make the console compete with the controller for the
// device, and the controller's capture is the evidence the whole screen rests on.
//
// WHEN THERE IS NO STREAM the panel does not quietly show the last picture and let it read as video. It says which
// kind of absence this is, in the shared card states, and shows the mission stills at the cadence they were captured
// at. An absent camera in a simulation that configured none is an expected fixture state and is NOT called degraded:
// see `model/camera.ts` for the eight readings and why they are not one word.
//
// THREE AGES, THREE CLOCKS. How long ago the picture was TAKEN is not known: the camera's clock and the runtime's
// have no measured relation, and this console never subtracts two monotonic clocks on two machines. How long ago the
// runtime RECEIVED the frame is measured in the runtime's clock alone. How long ago the line REACHED THIS TAB is this
// browser's own clock, on arrivals. The decision age is a different quantity again and belongs to the record that
// consumed the frame, not to this panel.
//
// THOSE THREE ROWS, AND THE REASONING UNDER THEM, ARE IN THE ENGINEERING TAB. Not deleted: `CameraClocksPanel` at the
// bottom of this file is the same three rows and the same paragraph, rendered in the telemetry group over there,
// which is where somebody who is debugging a clock goes looking. What they cost on the presentation screen was about
// seventy pixels above the fold, and the fold is where the current action and the phase have to be. What stays here
// is the part an operator acts on: the one honest fact that the capture age is not a knowable number, and the
// identity and age of the still itself.
//
// A STILL IS LABELLED AS ONE, WITH THE IDENTITY IT WAS CAPTURED UNDER. When there is no stream the picture is the
// newest frame the controller stored, and the caption carries what it is (a still, not video), which observation it
// is, which camera role took it, the camera session and sample it was stamped with where the record carries them, and
// how old it is. ITS AGE IS COUNTED IN RECORDS, not in seconds: the records are one ordered log with one sequence
// number, so "12 records ago" is a quantity this console can actually establish, while a duration between the
// runtime's wall clock and this browser's would be a number nobody measured. The recorded wall time is printed beside
// it as the runtime wrote it, unaltered and unsubtracted.

import type { ReactNode } from 'react';
import { useState } from 'react';
import { cameraReading, captureAgeSentence, proxiedMediaUrl } from '../model/camera';
import { integer, millis, number, UNAVAILABLE } from '../model/format';
import { overlayLayers } from '../model/overlay';
import { previewView } from '../model/sources';
import { previewReading, stillReading } from '../model/still';
import type { ConsoleSource } from '../api/types';
import type { StoreSnapshot } from '../store/store';
import { useNowMs } from '../store/useConsole';
import { Age } from '../ui/Age';
import { Badge } from '../ui/Badge';
import { IconCamera } from '../ui/Icons';
import { Field, Fields, Panel } from '../ui/Panel';
import { FrameView } from './FrameView';
import type { ScreenMode } from './StatusStrip';

/**
 * How long this console waits for another telemetry sample before it stops presenting the last one as current. Measured
 * in the BROWSER's clock, on the arrival of samples, and never subtracted from a capture age.
 */
const SILENT_FEED_MS = 4000;

/** Older than this and the arrival of the last preview line is drawn as stale rather than as current. */
const STALE_PREVIEW_MS = 1200;

/**
 * How long the TELEMETRY STREAM has been silent, on this computer's clock, or null when no line has ever arrived.
 *
 * What proves the stream is alive is the arrival of ANY telemetry line, tap or preview, not the preview alone. The
 * runtime sends the preview slot's defaults once when a subscriber attaches and then republishes that slot only when
 * something publishes a preview (demo_runtime.rpc.stream_telemetry over TelemetryHub, whose slots are latest-only).
 * With no preview source attached the slot is never sent again, while tap lines keep arriving every 100 ms.
 *
 * MEASURED against a running API: 140 telemetry frames reached the page in 14 s while this panel, reading the preview
 * arrival alone, said "No telemetry sample has arrived for 13.93 s" and put the live view into "feed silent". Both
 * were false, and they would be false on every launch today, because no preview source is attached to the runtime.
 */
export function telemetrySilentForMs(telemetry: { tap_received_at_ms: number | null; preview_received_at_ms: number | null }, nowMs: number): number | null {
  const last = Math.max(telemetry.preview_received_at_ms ?? 0, telemetry.tap_received_at_ms ?? 0);
  return last === 0 ? null : nowMs - last;
}

/**
 * HOW OLD A MISSION STILL IS, counted in records of the ordered log.
 *
 * It is not counted in seconds, and this is not a shortcoming to be fixed later. The still was stamped on the robot's
 * computer and written by the runtime; the only clock this browser owns is its own, and the three are not related by
 * anything measured. What IS shared is the one sequence number every record in the mission log carries, so the
 * distance between the still's record and the newest record folded here is a quantity both ends actually agree on.
 *
 * A cursor behind the still means the still arrived in a record this fold has not counted past yet, which happens for
 * one turn of the loop whenever a capture is the newest thing there is. That reads as the newest one rather than as a
 * negative age.
 */
export function stillAgeWords(stillSeq: number, cursorSeq: number): string {
  const behind = cursorSeq - stillSeq;
  if (behind <= 0) return 'the newest record in this fold';
  return `${String(behind)} record(s) before the newest in this fold`;
}

export function LiveViewPanel(props: { snapshot: StoreSnapshot; mode: ScreenMode; source: ConsoleSource; nowMs?: number }): ReactNode {
  const { view, telemetry } = props.snapshot;
  // One reading of this computer's clock for everything on this card, so two ages rendered together cannot disagree.
  const shared = useNowMs();
  const nowMs = props.nowMs ?? shared;

  const silentForMs = telemetrySilentForMs(telemetry, nowMs);
  const camera = previewView(telemetry.preview, telemetry.preview_received_at_ms, nowMs);
  const reading = cameraReading({
    camera,
    mode: props.mode,
    sourceKind: props.source.kind,
    silentForMs,
    silentAfterMs: SILENT_FEED_MS,
  });

  // The ONE stream this console ever opens, for the role the runtime published. A role is never guessed: with no role
  // there is nothing to ask the runtime for, and no picture is shown.
  const stream = proxiedMediaUrl(reading.stream && reading.role !== null ? props.source.previewUrl(reading.role) : null);

  // The newest retained capture, which is what this card shows when there is no stream. It is a stored frame served
  // by its observation id, so showing it holds nothing open on the camera. WHICH capture, and what to say when there
  // is none, is decided in `model/still.ts`: the fold first, the snapshot under it, and three different sentences for
  // the three different absences. This card asked the fold alone and so had nothing at all in a tab that connected
  // after the capture.
  const [imageFailedFor, setImageFailedFor] = useState<string | null>(null);
  const still = stillReading({
    observations: view.observations,
    snapshot_observation: view.snapshot_observation,
    evidence_gaps: view.evidence_gap_events,
    image_failed_for: imageFailedFor,
  });
  const preview = previewReading(reading, still.state === 'shown');
  const newest = still.still;
  const previousSeq = still.previous_seq;
  const stillUrl = newest === null ? null : props.source.frameUrl(newest.observation_id, 'raw', 640);
  const stillPx = newest?.width_px == null || newest.height_px == null ? null : { width: newest.width_px, height: newest.height_px };

  return (
    <Panel
      title="live physical view"
      state={reading.state}
      stateWords={reading.words}
      extra={
        <>
          <Badge tone="quiet" code title="a camera role, never a device id">
            <IconCamera /> {reading.role ?? 'role not stated'}
          </Badge>
          {reading.expected ? <Badge tone="quiet">expected here</Badge> : null}
          {reading.stream ? null : <Badge tone="quiet">{preview.words}</Badge>}
          {still.from === 'snapshot' ? <Badge tone="quiet">named by the runtime snapshot</Badge> : null}
        </>
      }
      note={
        // TWO STATEMENTS, NEVER ONE. The first is about video and the second is about the still, and the whole reason
        // they are written apart is that an operator was told there was no picture because there was no video.
        <>
          {preview.sentence} {still.sentence}
        </>
      }
    >
      {reading.stream && stream.url !== null ? (
        <div className="frame">
          {/* Not lazy and not keyed by anything that changes: this is a stream, and replacing the element restarts it. */}
          <img className="frame__image" src={stream.url} alt={`the ${reading.role ?? 'camera'} preview stream`} />
          {/* A stream is only ever shown in live mode, so this draws nothing today. It stays because the day that
              rule is relaxed is the day a moving picture must not be able to appear on this screen unmarked. */}
          {props.mode === 'live' ? null : <div className="frame__watermark">{props.mode ?? 'mode not recorded'}</div>}
          <div className="frame__caption">
            <span>{reading.role ?? 'role not stated'}</span>
            {/* The runtime's RECEIVE age, named as such. It used to be captioned "capture age", which is a different
                quantity in a different clock and is not computable across this path at all. */}
            <span>received by the runtime {millis(camera.receive_age_ms)} ago</span>
            <span>{number(camera.measured_fps, 1, 'fps')}</span>
            <span>camera seq {camera.source_seq === null ? UNAVAILABLE : String(camera.source_seq)}</span>
          </div>
        </div>
      ) : (
        <FrameView
          imageUrl={stillUrl}
          frameId={newest?.observation_id ?? null}
          widthPx={stillPx?.width ?? null}
          heightPx={stillPx?.height ?? null}
          perception={null}
          // No perception is fetched for a still: this card is not evidence for anything, and the decision frame is
          // where a perception is read. The layers are built for THIS observation all the same, so that nothing from
          // another frame could ever be handed to it.
          overlay={overlayLayers(null, stillPx, newest?.observation_id ?? null)}
          watermark={props.mode === 'live' ? null : props.mode}
          onImageError={setImageFailedFor}
          emptyNote={
            <>
              <strong>{props.source.kind === 'synthetic' ? 'No camera bytes in this page.' : `${still.words}.`}</strong>
              <span>
                {/* The SOURCE decides this sentence, not the runtime's data mode. The script inside this page carries no
                    camera bytes at all; a runtime in synthetic mode does serve stored frames (GET
                    /api/observations/{'{'}id{'}'}/frame answers with a jpeg), so saying it carries none would be false. */}
                {props.source.kind === 'synthetic'
                  ? 'The synthetic source carries no camera bytes. The scene drawn here is a placeholder, not a picture of anything.'
                  : still.sentence}
              </span>
            </>
          }
          caption={
            // With no still there is one thing to say and it is said once, in the words of the state that is actually
            // true. Four fields reading unavailable would say four things could not be reported, when what is true is
            // one of three quite different things about one capture.
            newest === null ? (
              <span>{still.words.toLowerCase()}</span>
            ) : (
              <>
                {/* WHAT IT IS, first and in its own words, so that nothing below can be read as a moving picture. */}
                <span className="frame__caption-lead">still, not video</span>
                {/* WHICH ONE IT IS. The observation id is the handle everything else on this screen uses for the same
                    frame; the role is the camera that took it, never a device id. The camera's own session and sample
                    number are its capture identity at the source, and they are printed only when the record carried
                    them: a frame stamped by no source has none, and inventing one would put a second identity on a
                    picture that only ever had one. */}
                <span>observation {newest.observation_id}</span>
                <span>{newest.source_role} camera</span>
                {newest.source_stamp === null ? (
                  <span>the record carries no camera stamp for it</span>
                ) : (
                  <span>
                    camera session {newest.source_stamp.session}, sample {String(newest.source_stamp.seq)}
                  </span>
                )}
                {/* HOW OLD IT IS, counted in the one thing both ends of this path agree on: records of the ordered
                    log. A duration between the runtime's wall clock and this browser's is not computed anywhere on
                    this screen, and it is not computed here either; the recorded wall time is shown as written. */}
                <span>
                  {stillAgeWords(newest.seq, view.seq_cursor)}
                  {newest.t_wall_utc === null ? '' : `, recorded at ${newest.t_wall_utc}`}
                </span>
                <span>cadence {previousSeq === null ? 'one still so far' : `1 still per ${String(newest.seq - previousSeq)} record(s)`}</span>
                {stream.refusal === null ? null : <span>stream not shown: {stream.refusal}</span>}
              </>
            )
          }
        />
      )}

      {/* The one fact from the three clock rows that an operator acts on, in one line: there is no knowable number
          for how long ago the picture was taken, so nothing on this card is claiming to be one. The three rows
          themselves, and the reasoning that gets to this sentence, are in the engineering tab under
          `CameraClocksPanel`, which is where the operator asked for the long explanations to live. */}
      <p className="muted" style={{ marginTop: '0.5rem', fontSize: 'var(--text-xs)' }}>
        {telemetry.preview === null
          ? 'No preview line has arrived, so there is no age for it to show in any clock. The still above carries its own identity and its own age in records.'
          : camera.capture_age_ms === null
            ? 'How long ago the camera took a picture is not a number this screen can state. The three ages that are measurable, each in its own clock, are in the engineering tab.'
            : `The runtime reported the capture age itself, in its own clock: ${millis(camera.capture_age_ms)}. The other two ages are in the engineering tab.`}
      </p>
    </Panel>
  );
}

/* ---- the clocks, in the engineering tab -------------------------------------------------------------------------- */

/**
 * THE THREE AGES AND WHY THEY ARE THREE, moved here from the presentation card and not changed on the way.
 *
 * The operator asked for the lengthy clock and provenance explanations to be in the engineering tab, and this is
 * them: the same three rows, the same reason the capture age is not a number, the same paragraph about what a still
 * is. Nothing was deleted. It is the honesty of the screen and it belongs one tab over, beside the source panels
 * that carry every other field the runtime states about the same two sources.
 *
 * Each age is in the clock it was taken in and they are never added together and never substituted for one another.
 * Before any preview line has arrived there are no ages at all, and that is said once rather than three times as
 * three different words for nothing.
 */
export function CameraClocksPanel(props: { snapshot: StoreSnapshot; nowMs?: number }): ReactNode {
  const { view, telemetry } = props.snapshot;
  const shared = useNowMs();
  const nowMs = props.nowMs ?? shared;
  const camera = previewView(telemetry.preview, telemetry.preview_received_at_ms, nowMs);
  // The same two sources in the same order as the presentation card, from the one place that decides it. A debugging
  // card that answered from the fold alone would report no still to the person debugging why there is no still.
  const newest = stillReading({
    observations: view.observations,
    snapshot_observation: view.snapshot_observation,
    evidence_gaps: view.evidence_gap_events,
    image_failed_for: null,
  }).still;

  return (
    <Panel
      title="the camera's clocks"
      extra={
        <Badge tone="quiet" code title="a camera role, never a device id">
          <IconCamera /> {camera.role ?? 'role not stated'}
        </Badge>
      }
      note="Three quantities, three clocks, one card. They are on this tab and not on the presentation screen because reading them is a debugging task, and the row of the screen they used to take is the row the current action and the phase need."
    >
      <Fields>
        {telemetry.preview === null ? (
          <Field label="preview lines">none has arrived, so there is no age to show in any clock</Field>
        ) : (
          <>
            <Field label="capture age">
              <span title={captureAgeSentence(camera)}>
                {camera.capture_age_ms === null ? 'not known: the camera clock and the runtime clock have no measured relation' : millis(camera.capture_age_ms)}
              </span>
            </Field>
            <Field label="received by the runtime">
              {camera.receive_age_ms === null ? UNAVAILABLE : `${millis(camera.receive_age_ms)} ago, in the runtime's clock`}
            </Field>
            <Field label="reached this tab">
              <Age atMs={telemetry.preview_received_at_ms} nowMs={nowMs} staleAfterMs={STALE_PREVIEW_MS} absent="no preview line has arrived" />
            </Field>
          </>
        )}
        {/* Only when the runtime reported it. It is an extension an older runtime does not send, and the source panel
            carries the full field list for a reader who wants the absences as well. */}
        {camera.skipped_for_mission === null ? null : (
          <Field label="yielded to the controller">
            {`${integer(camera.skipped_for_mission)} preview(s) stood aside for a decision capture, which is the camera serving the mission and not a fault`}
          </Field>
        )}
      </Fields>

      <p className="muted" style={{ marginTop: '0.5rem', fontSize: 'var(--text-xs)' }}>
        The capture age is the one of the three that is usually not a number at all. The camera stamps a frame on the
        robot&apos;s computer and the runtime measures its own receipt on the laptop, and nothing has ever measured a
        relation between those two clocks, so a subtraction across them would produce a figure nobody took. The
        runtime says so in its own reason code where it has one, which is what the row above carries.
      </p>

      <p className="muted" style={{ marginTop: '0.5rem', fontSize: 'var(--text-xs)' }}>
        What the still on the presentation card is. With no preview stream, that card shows the newest frame the
        controller stored and served back by its observation id, which costs the camera nothing and adds no second
        owner of the device. It is a still and the card says so; it is not a slowed-down video and no sequence of them
        is ever played as one. Its age is counted in records of the ordered log rather than in seconds, for the same
        reason the capture age is not a number.{' '}
        {newest === null
          ? 'No still has been retained in this run yet.'
          : `The newest is ${newest.observation_id}, from the ${newest.source_role} camera, at seq ${String(newest.seq)}, which is ${stillAgeWords(newest.seq, view.seq_cursor)}.`}
      </p>
    </Panel>
  );
}
