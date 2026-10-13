// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE CAMERA PANEL, ANSWERING FOR ITSELF.
//
// The imported design draws two camera panels side by side and gives each one a chip with four possible words:
// SAMPLE, LIVE, STALE and DISCONNECTED. Three of those four are prototype vocabulary. This runtime reports NINE
// distinct source states (`model/sources.ts`), and the difference between them is the difference between a camera
// somebody has to go and look at and a camera nobody ever configured. So the design's CHIP is kept and its WORD LIST
// is not: the word is the runtime's own state, and the sentence under it is the runtime's own sentence.
//
// `SAMPLE` in particular is never shown for a real camera. It exists in the prototype because the prototype had no
// cameras; the one thing on this screen that is genuinely sample media is the synthetic source, which says so with
// its own word.
//
// THREE PICTURES, IN THIS ORDER, AND NEVER SUBSTITUTED FOR ONE ANOTHER:
//
//   THE STREAM, when the runtime reports this role streamable AND lines are still reaching this browser. It is the
//   only case that may be called live video, and even then only once the picture element of the current attempt has
//   DECODED a frame (`model/streamDecode.ts`). The runtime reporting frames is metadata about the runtime; it is not a
//   picture on this screen, and the chip said LIVE over a black panel while it was read as one.
//   THE STILL that THIS role captured, when there is no stream, and ONLY ON A LIVE RUNTIME. It is a stored frame fetched
//   by observation id and it costs the camera nothing. It is labelled a still every time it is drawn. A rehearsal's
//   stored frames are generated, not taken by a camera, and they are shown in Evidence with a GENERATED label and in no
//   camera panel at all (the design review's ruling).
//   NOTHING, with the state's own sentence. The design covers the picture with a message in this case, which is right
//   when there is no picture and wrong when there is one, so the cover is drawn only here, and over a stream that has
//   not decoded a picture yet.
//
// A PANEL NEVER FALLS BACK TO THE OTHER ROLE. `telemetry.preview` is the wrist sample mirrored for single-camera
// readers (`store.ts`), so building the second panel from it would draw one camera twice. Everything here is keyed by
// role, including the still: an observation belongs to the role its own record names.

import type { ConsoleSource } from '../api/types';
import { proxiedMediaUrl } from '../model/camera';
import { deliveredFps, fpsReadiness, FRAME_WINDOW_MS } from '../model/frameProgression';
import { ageFromWallUtc } from '../model/measurement';
import { availabilitySentence, previewView } from '../model/sources';
import type { PreviewView } from '../model/sources';
import type { ObservationMeta, ViewState } from '../model/types';
import { attemptUrl, NEARLY_BLACK_LUMINANCE, showingVideo, unchangedWhileArrivingMs } from '../model/streamDecode';
import type { StreamDecode } from '../model/streamDecode';
import type { StoreSnapshot } from '../store/store';
import { asSentence } from './actionCard';
import { C, wash } from './theme';

/** What the chip says, in the runtime's vocabulary rather than the prototype's. */
const STATE_WORD: Record<PreviewView['availability'], string> = {
  connected: 'LIVE',
  connecting: 'CONNECTING',
  stalled: 'STALLED',
  disconnected: 'DISCONNECTED',
  not_attached: 'NOT CONFIGURED',
  failed: 'FAILED',
  silent: 'SILENT',
  never_arrived: 'NO LINE',
  unstated: 'UNSTATED',
};

/** The design's three tones, assigned by what the state means for somebody watching. */
function toneOf(availability: PreviewView['availability'], streaming: boolean): string {
  if (streaming) return C.ok;
  switch (availability) {
    case 'connecting':
      return C.accent;
    case 'stalled':
    case 'silent':
    case 'unstated':
      return C.warn;
    case 'not_attached':
    case 'never_arrived':
      return C.mute;
    default:
      return C.bad;
  }
}

/**
 * The newest retained frame THIS ROLE captured.
 *
 * The fold comes first and the snapshot's own named frame is the fallback under it, which is what gives a tab that
 * opened or reloaded after a capture something to draw. Both are filtered by `source_role`, so the wrist panel can
 * never borrow the front camera's picture.
 */
export function newestStillFor(view: ViewState, role: string): ObservationMeta | null {
  let best: ObservationMeta | null = null;
  for (const item of Object.values(view.observations)) {
    if (item.source_role !== role) continue;
    if (item.media !== 'frame' && item.media !== 'frame_pair') continue;
    if (best === null || item.seq > best.seq) best = item;
  }
  const named = view.snapshot_observation;
  if (named !== null && named.source_role === role && (best === null || named.seq > best.seq)) best = named;
  return best;
}

/**
 * WHAT A STORED FRAME IS, which decides whether one may be drawn in a camera panel at all.
 *
 * `saved`: a live runtime's retained observation, a real camera's picture from earlier, drawn with its age and marked
 * as not current. It is the only kind a camera panel draws. A SIMULATION runtime's frames were drawn by the fake
 * controller (a grey floor, a light pad and a yellow cube) and no camera took them, so by the design review's ruling they
 * appear in Evidence, labelled GENERATED, and never in a camera panel: a camera panel that shows a picture reads as a
 * camera, whatever its caption says. A replay, or a mode the runtime did not state, draws no stored frame either.
 */
export type StillProvenance = 'saved';

export function stillProvenance(view: ViewState): StillProvenance | null {
  return view.data_mode === 'live' ? 'saved' : null;
}

/** The cover a camera panel draws on a rehearsal: there is no camera, and the generated frames are in Evidence. */
export const REHEARSAL_COVER = {
  title: 'No camera in a rehearsal',
  note: 'This runtime is a rehearsal and has no camera. Any frame it generated is shown in Evidence, labelled GENERATED, and never here.',
};

export interface CameraPanelModel {
  role: string;
  /** The chip word: the runtime's own state, uppercased. */
  word: string;
  tone: string;
  chipBg: string;
  /** The short note beside the chip. The runtime's reason where it sent one. */
  note: string;
  /** The moving picture, when there is one. */
  streamUrl: string | null;
  /** The stored frame, when there is no stream and this role captured one. */
  stillUrl: string | null;
  still: ObservationMeta | null;
  /** The cover, drawn ONLY when there is neither a stream nor a still. */
  cover: { title: string; note: string } | null;
  /**
   * The RUNTIME's measured rate, over frames the RUNTIME received. Null when it reported none, and null once no
   * frame has reached this browser inside the rate window: the runtime's number is frozen the moment the line to
   * this tab dies, so shown ungated it outlives the feed it describes and reads as a camera still running at 30 Hz.
   */
  fps: number | null;
  /** The rate of frames delivered to THIS TAB, counted here from frame identity. Null below two in the window. */
  deliveredFps: number | null;
  /** True while the rate window is still filling, so the screen says it is measuring rather than print a number. */
  measuringRate: boolean;
  /** When the last line of this role reached THIS browser, on this computer's clock. Transport activity. */
  lastLineAgeMs: number | null;
  /**
   * WHEN A NEW FRAME of this role last reached this browser, on this computer's clock.
   *
   * This is the quantity that notices a stalled camera while the runtime goes on answering about it. It is NOT the
   * age of the picture: that would need a measured relation between the camera's clock and this one, which nothing
   * here has and `model/sources.ts` refuses to invent. Null when no frame has ever been counted for this role.
   */
  lastFrameAgeMs: number | null;
  /**
   * WHEN THE SAVED PICTURE WAS RECORDED, from the observation's OWN wall time. Null when the record carries none.
   *
   * It is kept apart from `lastLineAgeMs` on purpose. That one says when a telemetry LINE last arrived, which for a
   * role whose preview is not configured is a status line that arrives constantly; printing it under a stored frame
   * made a picture from a run hours ago read as something that had just happened.
   */
  stillRecordedAgeMs: number | null;
  stillRecordedAt: string | null;
  /** What the still is, when there is one. Only a live runtime's saved frame is ever drawn in a camera panel. */
  stillKind: StillProvenance | null;
  /**
   * True only when the picture element of the current attempt has DECODED a frame and has not reported an error since.
   * It is the only basis for the word LIVE; the runtime's frame metadata never is.
   */
  showingVideo: boolean;
  /** The attempt the stream element is on, from 1. Null when there is no stream to attempt. */
  attempt: number | null;
}

/**
 * The stream the runtime offers for this role, as the address the panel's element attempts, or null when it offers
 * none. It is what a new attempt sequence is keyed on: the runtime offering a stream again starts over at attempt 1.
 */
export function cameraStreamBase(snapshot: StoreSnapshot, role: string, source: ConsoleSource, nowMs: number): string | null {
  const sample = snapshot.telemetry.previews[role] ?? null;
  const camera = previewView(sample, snapshot.telemetry.preview_received_at_ms_by_role[role] ?? null, nowMs);
  return camera.streamable ? proxiedMediaUrl(source.previewUrl(role)).url : null;
}

/**
 * One panel. `decode` is what the panel's own picture element has shown in this tab (`model/streamDecode.ts`), or null
 * when no attempt has been made; a stream with no decode state has decoded nothing and is never called LIVE.
 */
export function cameraPanel(
  snapshot: StoreSnapshot,
  role: string,
  source: ConsoleSource,
  nowMs: number,
  decode: StreamDecode | null = null,
): CameraPanelModel {
  const sample = snapshot.telemetry.previews[role] ?? null;
  const receivedAt = snapshot.telemetry.preview_received_at_ms_by_role[role] ?? null;
  const camera = previewView(sample, receivedAt, nowMs);
  // FRAME progression, kept apart from line arrival. `frameAt` moves only when a picture this tab had not already
  // counted arrived; the STALLED heartbeat moves `receivedAt` above and must not move this.
  const frameAt = snapshot.telemetry.preview_frame_at_ms_by_role[role] ?? null;
  const frameAgeMs = frameAt === null ? null : Math.max(0, nowMs - frameAt);
  const times = snapshot.telemetry.preview_frame_times_by_role[role] ?? [];
  // WARMING UP IS NOT A RATE. For the first window of a subscription the estimator divides over a handful of closely
  // spaced arrivals, which reads as a rate well above the source's cap; the panel says it is still measuring instead.
  // The steady-state calculation below is unchanged, and this only decides whether to publish its answer.
  const readiness = fpsReadiness(times, snapshot.telemetry.preview_first_frame_at_ms_by_role[role] ?? null, nowMs);
  const delivered = readiness === 'ready' ? deliveredFps(times, nowMs) : null;
  // The runtime's rate describes a window of frames it received. Once nothing has reached THIS tab inside the window
  // it is a reading about a feed that is no longer arriving, so it stops being offered as current.
  const runtimeFps = frameAgeMs !== null && frameAgeMs <= FRAME_WINDOW_MS ? camera.measured_fps : null;
  const stream = proxiedMediaUrl(camera.streamable ? source.previewUrl(role) : null);
  const streaming = camera.streamable && stream.url !== null;

  const provenance = stillProvenance(snapshot.view);
  const still = streaming || provenance === null ? null : newestStillFor(snapshot.view, role);
  const stillUrl = still === null ? null : source.frameUrl(still.observation_id, 'raw', 960);

  const reason = camera.reason ?? stream.refusal;
  const sentence = availabilitySentence(camera.availability);
  const video = streaming && showingVideo(decode);
  const streamWords = streaming ? streamState(decode, nowMs) : null;
  const tone = streamWords !== null && !video ? streamWords.tone : toneOf(camera.availability, streaming);

  const rehearsalCover = streamWords === null && stillUrl === null && provenance === null && snapshot.view.data_mode === 'synthetic';
  return {
    role,
    word: streamWords !== null ? streamWords.word : plainWord(camera.availability, reason, rehearsalCover),
    tone,
    chipBg: wash(tone, 0.12),
    // EACH FACT ONCE. The chip already carries the state word, so the note beside it carries the runtime's REASON
    // CODE, which is the part the word does not contain, and is empty when the runtime sent none. The sentence that
    // explains the state belongs on the cover, where there is room for it and where somebody is looking because there
    // is no picture. Putting the sentence in both places is what this read as before, and it looked like a stutter.
    note: streamWords !== null ? streamWords.note : plainNote(camera.availability, reason, camera.receive_age_ms),
    // EACH ATTEMPT IS ITS OWN REQUEST. The API reads `role` and nothing else, so the attempt number makes a retry a new
    // stream rather than an answer the browser already holds.
    streamUrl: streaming && stream.url !== null ? attemptUrl(stream.url, decode?.attempt ?? 1) : null,
    stillUrl: stillUrl === null ? null : stillUrl,
    still,
    cover:
      streamWords !== null
        ? streamWords.cover
        : stillUrl !== null
          ? null
          : rehearsalCover
            ? REHEARSAL_COVER
            : { title: 'No picture from this camera', note: reason === null ? sentence : `${sentence}: ${reason}` },
    fps: runtimeFps,
    deliveredFps: delivered,
    measuringRate: readiness === 'measuring',
    lastLineAgeMs: camera.silent_for_ms,
    lastFrameAgeMs: frameAgeMs,
    stillRecordedAgeMs: still === null ? null : ageFromWallUtc(still.t_wall_utc, nowMs),
    stillRecordedAt: still?.t_wall_utc ?? null,
    stillKind: stillUrl === null ? null : provenance,
    showingVideo: video,
    attempt: streaming ? (decode?.attempt ?? 1) : null,
  };
}

/** The runtime's reasons that say no preview was CONFIGURED, as against a configured one whose source is not attached. */
const NOT_CONFIGURED_REASONS: ReadonlySet<string> = new Set(['preview_not_configured', 'tap_not_configured']);

/**
 * THE CHIP WORD WITHOUT A STREAM. LIVE is never drawn here: a runtime that reports frames while this panel shows no
 * stream says the frames are reported, and a rehearsal's panel, which has no camera, says so. A source that is not
 * attached is named apart from a preview that was never configured, by the runtime's own reason.
 */
function plainWord(availability: PreviewView['availability'], reason: string | null, rehearsalCover: boolean): string {
  if (availability === 'connected') return rehearsalCover ? 'NO CAMERA' : 'FRAMES REPORTED';
  if (availability === 'not_attached') return reason !== null && NOT_CONFIGURED_REASONS.has(reason) ? 'NOT CONFIGURED' : 'NOT ATTACHED';
  return STATE_WORD[availability];
}

/** The note beside the chip: the runtime's reason code, and for a stall how long ago the runtime last received a frame. */
function plainNote(availability: PreviewView['availability'], reason: string | null, receiveAgeMs: number | null): string {
  if (availability !== 'stalled') return reason ?? '';
  const age = receiveAgeMs === null ? 'the runtime did not say when it last received a frame' : `the runtime last received a frame ${(receiveAgeMs / 1000).toFixed(1)} s ago`;
  return `${reason ?? 'the runtime gave no reason'}; ${age}`;
}

/**
 * WHAT A STREAMABLE CAMERA'S PANEL SAYS, from what its picture element has shown in THIS tab.
 *
 * The runtime reporting the role streamable is why there is an element at all. What the chip says is the element's:
 * LIVE once the current attempt has decoded a frame, CONNECTING before that, and the truthful non-live word when an
 * attempt failed or its stream broke (an `error` from the element), with when the next attempt will be made. The cover
 * is drawn over everything that is not decoded video, so a panel with no picture never looks like a dark room.
 *
 * A DECODED PICTURE THAT HAS NOT CHANGED STAYS LIVE. It is still what this tab decoded and shows, so the note says how
 * long it has been the same while the runtime reports new frames, and nothing more: that evidence does not show the
 * stream ended, and a still scene or a covered lens gives the same picture while the stream is fine.
 */
function streamState(
  decode: StreamDecode | null,
  nowMs: number,
): { word: string; note: string; tone: string; cover: { title: string; note: string } | null } {
  const attempt = decode?.attempt ?? 1;
  if (decode !== null && showingVideo(decode)) {
    const facts = ['decoded in this tab'];
    // A PICTURE THAT DECODED DARK IS STILL A PICTURE. It is said to be nearly black, so a black panel is read as what
    // the camera sees and not as a stream that is not arriving.
    if (decode.luminance !== null && decode.luminance < NEARLY_BLACK_LUMINANCE) facts.push('the picture is nearly black');
    const unchangedMs = unchangedWhileArrivingMs(decode);
    if (unchangedMs !== null) {
      facts.push(`the picture has not changed for ${String(Math.round(unchangedMs / 1000))} s while the runtime reports new frames`);
    }
    return {
      word: 'LIVE',
      note: facts.join('; '),
      tone: C.ok,
      cover: null,
    };
  }
  const retry =
    decode === null || decode.retryAtMs === null
      ? ''
      : ` The next attempt is in ${String(Math.max(1, Math.ceil((decode.retryAtMs - nowMs) / 1000)))} s.`;
  if (decode !== null && decode.phase === 'failed') {
    return {
      word: 'NO PICTURE',
      note: `attempt ${String(attempt)} failed`,
      tone: C.warn,
      cover: {
        title: 'No picture decoded in this tab',
        note: `The runtime reports this camera streaming, and ${decode.why ?? 'the stream did not show a picture'} (attempt ${String(attempt)}).${retry}`,
      },
    };
  }
  if (decode !== null && decode.phase === 'broken') {
    return {
      word: 'STREAM BROKE',
      note: `attempt ${String(attempt)} broke`,
      tone: C.warn,
      cover: {
        title: 'The stream broke in this tab',
        note: `${asSentence(`${decode.why ?? 'the stream reported an error'} (attempt ${String(attempt)})`)}${retry}`,
      },
    };
  }
  return {
    word: 'CONNECTING',
    note: 'no frame decoded here yet',
    tone: C.accent,
    cover: {
      title: 'Opening the camera stream',
      note: `The runtime reports this camera delivering frames. This tab has not decoded one from the stream yet (attempt ${String(attempt)}).`,
    },
  };
}
