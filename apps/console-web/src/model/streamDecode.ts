// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHETHER THIS TAB IS ACTUALLY SHOWING VIDEO, decided from the picture element and not from the telemetry about it.
//
// The camera panel's chip used to say LIVE, and its note "stream advancing", whenever the runtime reported the role
// streamable. That is METADATA: the runtime says frames are arriving at IT. It says nothing about whether the <img> in
// this tab ever decoded one. On the robot stack the two came apart: the telemetry kept reporting frames while the
// panel showed a black area, and nothing on the screen said so. The operator's ruling is that advancing metadata alone
// does not prove visible video.
//
// WHAT CHROME ACTUALLY DOES with a multipart/x-mixed-replace <img>, MEASURED in the headless Chrome this project's
// browser checks use (153): `load` fires ONCE, after the first part decodes, with naturalWidth set; `error` fires when
// the answer is not an image (the API answers 503 with a JSON body when no first frame arrived within its deadline);
// and NOTHING fires when the stream ends. The element keeps the last picture it decoded, forever. So:
//
//   LIVE rests on a decode of THIS attempt: `load` with naturalWidth > 0, or naturalWidth > 0 read off the element.
//   Each attempt is a NEW element, so a picture decoded by an earlier attempt can never stand for this one.
//   A FAILED attempt (`error`, or no decode within OPEN_TIMEOUT_MS) is said to be failed and retried after a bounded
//   backoff. The API ignores query parameters it does not read, so each attempt carries its own `attempt=` and is a
//   new request, never a cached answer.
//   An `error` AFTER a decode is a stream that broke: said so, its picture dropped, and retried on the same backoff.
//   AN UNCHANGED PICTURE IS NOT A STREAM THAT ENDED. The decoded pixels are sampled, and a picture that stays the same
//   across UNCHANGED_SAMPLES consecutive samples while the runtime reports new frames is REPORTED as exactly that, with
//   how long it has been the same: the picture stays, the attempt stays, and nothing is retried. A still scene, a
//   covered lens and a dark room can give the same picture sample after sample while the stream is fine, and an
//   earlier version that read it as an ended stream dropped a good picture and flapped LIVE, NOT ADVANCING, LIVE on a
//   static view. What the panel can say from this evidence is what it saw, not why.
//
// This file is the state machine and nothing else. It touches no DOM and no clock: the panel feeds it what the element
// did and what time it is, which is what lets it be pinned by a test.

import { utcStamp } from './utcTime';

/** What the current attempt has shown. */
export type DecodePhase = 'opening' | 'decoded' | 'failed' | 'broken';

export interface StreamDecode {
  /** Which attempt this is, from 1. Each attempt is a new element and a new request. */
  attempt: number;
  phase: DecodePhase;
  /** When this attempt's element was put on the page, on this computer's clock. */
  startedAtMs: number;
  /** When this attempt first decoded a picture (naturalWidth > 0). Null until it has. */
  decodedAtMs: number | null;
  /** The last pixel sample of the decoded picture, and when it was taken. */
  sample: string | null;
  sampledAtMs: number | null;
  /**
   * How many samples in a row have been the same as the one before, each taken while the runtime reported new frames,
   * and when the first sample of that run was taken. Zero and null when the picture changed, or when the runtime's
   * frames were not arriving, at the last sample. It is reported, never acted on.
   */
  unchangedSamples: number;
  unchangedSinceMs: number | null;
  /** The mean luminance of the last sample, 0 to 255, or null when none could be taken. */
  luminance: number | null;
  /** Attempts that failed or broke in a row. It sets the backoff, and a decode resets it. */
  failures: number;
  /** When the next attempt will be made, for a failed or broken attempt. Null otherwise. */
  retryAtMs: number | null;
  /** Why the last attempt was given up, in words, for the panel. Null while none was. */
  why: string | null;
}

/** How long an attempt may go without decoding a picture before it is counted as failed. */
export const OPEN_TIMEOUT_MS = 10_000;

/** How often the decoded picture is sampled, and how many identical samples in a row the panel reports as unchanged. */
export const SAMPLE_EVERY_MS = 2000;
export const UNCHANGED_SAMPLES = 3;

/**
 * THE BACKOFF, BOUNDED. The waits grow and stop growing at the last one, so a camera that is gone for an hour costs one
 * small request every half minute, and one that comes back is picked up within that.
 */
export const RETRY_DELAYS_MS: readonly number[] = [1000, 2000, 4000, 8000, 15_000, 30_000];

/** How recently the runtime must have handed this tab a new frame for an unchanged picture to be reported as one. */
export const FRAMES_ARRIVING_WITHIN_MS = 2000;

const LONGEST_WAIT_MS = 30_000;

export function retryDelayMs(failures: number): number {
  const index = Math.min(Math.max(failures, 1), RETRY_DELAYS_MS.length) - 1;
  return RETRY_DELAYS_MS[index] ?? LONGEST_WAIT_MS;
}

export function firstAttempt(nowMs: number): StreamDecode {
  return {
    attempt: 1,
    phase: 'opening',
    startedAtMs: nowMs,
    decodedAtMs: null,
    sample: null,
    sampledAtMs: null,
    unchangedSamples: 0,
    unchangedSinceMs: null,
    luminance: null,
    failures: 0,
    retryAtMs: null,
    why: null,
  };
}

/** The element of THIS attempt reported a picture. Only naturalWidth > 0 is a decode; a zero-width load is not. */
export function decoded(state: StreamDecode, naturalWidth: number, nowMs: number): StreamDecode {
  if (naturalWidth <= 0 || state.phase !== 'opening') return state;
  return { ...state, phase: 'decoded', decodedAtMs: nowMs, failures: 0, why: null };
}

function giveUp(state: StreamDecode, why: string, nowMs: number): StreamDecode {
  const failures = state.failures + 1;
  return { ...state, phase: state.phase === 'decoded' ? 'broken' : 'failed', failures, why, retryAtMs: nowMs + retryDelayMs(failures) };
}

/** The element of THIS attempt reported an error: the answer was not a picture, or the connection failed. */
export function errored(state: StreamDecode, nowMs: number): StreamDecode {
  if (state.phase === 'failed' || state.phase === 'broken') return state;
  // An error after a decode is a stream that broke, and the picture it left is not current either.
  return giveUp(state, state.phase === 'decoded' ? 'the stream broke after it had shown a picture' : 'the stream request did not return a picture', nowMs);
}

/**
 * One sample of the decoded picture. `checksum` is a digest of a small downscaled draw of it, or null when the pixels
 * could not be read (a picture the canvas may not read is never judged). `framesArrivingAgeMs` is how long ago the
 * runtime last handed this tab a NEW frame of this role, from the telemetry, or null when it never has.
 *
 * A sample never changes the phase. An unchanged picture is counted so the panel can say so; it does not end the
 * attempt, drop the picture or schedule a retry, because it is not evidence that the stream ended.
 */
export function sampled(
  state: StreamDecode,
  checksum: string | null,
  luminance: number | null,
  framesArrivingAgeMs: number | null,
  nowMs: number,
): StreamDecode {
  if (state.phase !== 'decoded' || checksum === null) return state;
  const arriving = framesArrivingAgeMs !== null && framesArrivingAgeMs <= FRAMES_ARRIVING_WITHIN_MS;
  const unchanged = arriving && state.sample !== null && state.sample === checksum;
  return {
    ...state,
    sample: checksum,
    sampledAtMs: nowMs,
    luminance,
    unchangedSamples: unchanged ? state.unchangedSamples + 1 : 0,
    unchangedSinceMs: unchanged ? (state.unchangedSinceMs ?? state.sampledAtMs) : null,
  };
}

/**
 * How long the decoded picture has been measured the same while the runtime reported new frames, from the first to the
 * last of those samples, or null when that is not established (fewer than UNCHANGED_SAMPLES identical samples in a row,
 * a change, frames not arriving, or no decoded picture). It describes the picture, not the stream.
 */
export function unchangedWhileArrivingMs(state: StreamDecode | null): number | null {
  if (state === null || !showingVideo(state) || state.unchangedSamples < UNCHANGED_SAMPLES) return null;
  if (state.unchangedSinceMs === null || state.sampledAtMs === null) return null;
  return Math.max(0, state.sampledAtMs - state.unchangedSinceMs);
}

/** A clock tick: an attempt that has decoded nothing within OPEN_TIMEOUT_MS is counted as failed. */
export function ticked(state: StreamDecode, nowMs: number): StreamDecode {
  if (state.phase === 'opening' && nowMs - state.startedAtMs >= OPEN_TIMEOUT_MS) {
    return giveUp(state, `no picture was decoded within ${String(OPEN_TIMEOUT_MS / 1000)} s`, nowMs);
  }
  return state;
}

/** True when a failed or broken attempt's wait is over and the next attempt should be made. */
export function retryDue(state: StreamDecode, nowMs: number): boolean {
  return state.retryAtMs !== null && nowMs >= state.retryAtMs;
}

/** The next attempt: a new element, a new request, and nothing carried over but the failure count for the backoff. */
export function nextAttempt(state: StreamDecode, nowMs: number): StreamDecode {
  return { ...firstAttempt(nowMs), attempt: state.attempt + 1, failures: state.failures, why: state.why };
}

/** True only when THIS attempt decoded a picture and its element has not reported an error. The only basis for LIVE. */
export function showingVideo(state: StreamDecode | null): boolean {
  return state !== null && state.phase === 'decoded' && state.decodedAtMs !== null;
}

/**
 * True while the attempt's picture element belongs on the page: opening, or decoded (an unchanged picture included). A
 * failed or broken attempt's element is dropped, and the next attempt mounts a new one when its wait is over.
 */
export function elementMounted(state: StreamDecode | null): state is StreamDecode {
  return state !== null && (state.phase === 'opening' || state.phase === 'decoded');
}

/** A decoded picture this dark is reported as such, so a black panel is never read as a stream that is not arriving. */
export const NEARLY_BLACK_LUMINANCE = 12;

/** The stream URL for one attempt. The API reads only `role` and ignores the rest, so this is a new request each time. */
export function attemptUrl(url: string, attempt: number): string {
  return `${url}${url.includes('?') ? '&' : '?'}attempt=${String(attempt)}`;
}

/**
 * THE LAST PICTURE THIS PAGE DECODED, kept through a gap in the stream.
 *
 * A failed or broken attempt drops its element, and the panel used to go black until the next attempt decoded. The
 * picture kept here is a COPY taken on this page while the stream was showing video, at the time stamped on it, and it
 * is drawn only while the stream is NOT showing video, marked STALE, with its age on this computer's clock and the
 * runtime's own reason for the gap. It is never the word LIVE and never counted as a frame delivered.
 */
export interface KeptPicture {
  url: string;
  /** When the copy was taken, on this computer's clock. */
  at_ms: number;
}

function keptAgeWords(ageMs: number): string {
  const seconds = Math.max(0, Math.round(ageMs / 1000));
  if (seconds < 90) return `${String(seconds)} s`;
  return `${String(Math.floor(seconds / 60))} min ${String(seconds % 60)} s`;
}

export function keptPictureView(showing: boolean, kept: KeptPicture | null, nowMs: number, reason: string): { url: string; words: string } | null {
  if (showing || kept === null) return null;
  return { url: kept.url, words: `STALE · last picture decoded here ${keptAgeWords(nowMs - kept.at_ms)} ago. ${reason}` };
}

/**
 * THE KEPT PICTURE ON THE DEMO SCREEN: the same copy, said with the fixed UTC time this page decoded it, never an age
 * (rules 5.4, 6.8): nothing on the screen changes while nothing new arrives. The time is this display's clock, and the
 * words say so. `iso` is the full time with milliseconds, for the element's `data-t` and title.
 */
export function keptPictureAtView(showing: boolean, kept: KeptPicture | null, referenceDay: string | null): { url: string; words: string; iso: string } | null {
  if (showing || kept === null) return null;
  const stamp = utcStamp(new Date(kept.at_ms).toISOString(), referenceDay);
  if (stamp === null) return null;
  return { url: kept.url, words: `The last picture this page decoded, at ${stamp.text} (this display's clock); not live.`, iso: stamp.iso };
}
