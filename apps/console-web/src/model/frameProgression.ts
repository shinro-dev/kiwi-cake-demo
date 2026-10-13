// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHETHER A PREVIEW LINE CARRIED A NEW FRAME, decided from the frame's own identity and from nothing else.
//
// The store used to stamp a preview RECEIPT every time a preview line arrived. The runtime publishes a preview line
// per frame while a source is CONNECTED, but it also heartbeats a frame-LESS status line about once a second while a
// source is STALLED, carrying the last frame's sequence number so a reader can see where it stopped. Both lines
// refreshed the same stamp, so a camera that had stopped delivering read as "last line just now" for as long as the
// runtime kept answering. The whole point of the quantity is to notice exactly that case.
//
// FOUR RULES, and each exists because of a way a feed really behaves:
//
//   A SLOT THAT IS NOT DELIVERING CARRIES NO FRAME. `available` false is the runtime saying it has no picture to give,
//   and such a slot may still carry the LAST frame's seq. That is a record of where the feed stopped, not a delivery.
//   SEQUENCE MUST ADVANCE WITHIN A SESSION. A repeated or lower seq is a duplicate or an out-of-order line, not a new
//   frame. Neither may move the age forward.
//   A NEW SESSION IS A NEW FRAME, whatever its seq. The camera service numbers from zero again when it restarts, so
//   the first frame of a new session usually has a LOWER seq than the last frame of the old one. Comparing seq across
//   a restart would freeze the age until the new session caught up, which on a long-lived session is minutes.
//   PIXELS ARE NEVER CONSULTED. A camera looking at a motionless scene delivers frames that are byte-identical, and
//   those are genuinely new captures. Content can only ever under-count, so it is not used, here or anywhere else.
//
// WHAT THIS IS NOT. It does not measure the age of the picture. It measures when a new frame last reached THIS
// browser, in this browser's clock. The capture age would need a measured relation between the camera's clock and
// this one, which `model/sources.ts` already refuses to invent.

import type { PreviewSample } from '../api/types';

/** The identity of one delivered frame. Never its content. */
export interface FrameIdentity {
  /** The camera service session the frame was numbered in, or null when the runtime published none. */
  session: string | null;
  /** The runtime's preview counter for this frame. */
  seq: number | null;
  /** The camera's own frame number, kept because it survives a runtime restart that the preview counter does not. */
  sourceSeq: number | null;
}

function counter(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * The identity of the frame a sample delivered, or null when it delivered none.
 *
 * Null for a slot that is not available, and null for one that carries no number at all: an identity that cannot be
 * compared cannot establish that anything advanced, and a frame nobody can identify must not move the age.
 */
export function frameIdentityOf(sample: PreviewSample | null | undefined): FrameIdentity | null {
  if (sample === null || sample === undefined) return null;
  if (sample.available !== true) return null;
  const seq = counter(sample.seq);
  const sourceSeq = counter((sample as { source_seq?: unknown }).source_seq);
  const session = typeof sample.capture_clock?.session === 'string' ? sample.capture_clock.session : null;
  if (seq === null && sourceSeq === null && session === null) return null;
  return { session, seq, sourceSeq };
}

/**
 * Whether `next` is a frame this browser has not already counted.
 *
 * `previous` is the identity of the last frame counted for the SAME role. Roles are numbered independently and are
 * never compared with each other; the caller keeps one previous identity per role.
 */
export function isNewFrame(previous: FrameIdentity | null, next: FrameIdentity | null): boolean {
  if (next === null) return false;
  if (previous === null) return true;
  // A restart renumbers from zero, so the session decides before any counter is looked at. Only a session that both
  // sides state can decide it: one unknown session is not evidence of a restart.
  if (previous.session !== null && next.session !== null && previous.session !== next.session) return true;
  // Within one session the runtime's preview counter is the authority.
  if (previous.seq !== null && next.seq !== null) return next.seq > previous.seq;
  // Without it, the camera's own frame number answers the same question.
  if (previous.sourceSeq !== null && next.sourceSeq !== null) return next.sourceSeq > previous.sourceSeq;
  // One side carries a number the other does not. Nothing here establishes an advance, so nothing is claimed.
  return false;
}

/** How many frame arrivals the browser keeps per role to measure a rate. A few seconds at 30 Hz. */
export const FRAME_WINDOW_MS = 5000;
const MAX_KEPT = 256;

/**
 * Frame arrival times in the BROWSER's clock, newest last, trimmed to the rate window.
 *
 * This is the delivered-frame rate as THIS TAB saw it, which is a different quantity from the runtime's
 * `measured_fps`: that one counts frames the RUNTIME received and cannot fall when the line to this browser dies.
 */
export function withFrameAt(times: readonly number[], atMs: number, windowMs: number = FRAME_WINDOW_MS): number[] {
  const kept = [...times, atMs].filter((time) => atMs - time <= windowMs);
  return kept.length > MAX_KEPT ? kept.slice(kept.length - MAX_KEPT) : kept;
}

/**
 * Frames per second delivered to this tab, or null.
 *
 * Null below two arrivals in the window, and null once the window holds nothing: a feed that stopped reads as
 * unavailable with a growing frame age, and NEVER as the rate it used to have. That is the same rule the runtime
 * applies to its own measurement, kept deliberately identical so the two numbers mean the same thing.
 */
export function deliveredFps(times: readonly number[], nowMs: number, windowMs: number = FRAME_WINDOW_MS): number | null {
  const recent = times.filter((time) => nowMs - time <= windowMs);
  const first = recent[0];
  const last = recent[recent.length - 1];
  if (recent.length < 2 || first === undefined || last === undefined) return null;
  const span = last - first;
  if (span <= 0) return null;
  return Math.round(((recent.length - 1) * 1000) / span * 1000) / 1000;
}

/**
 * The fewest distinct frames this tab will compute a rate from.
 *
 * Two is enough to divide by and not enough to mean anything: two arrivals that happen to land close together give an
 * arbitrarily large quotient. The floor is reachable inside one window at every preview rate the camera service will
 * accept (its `--preview-fps` is bounded to 0.2 to 15), so it delays a number rather than withholding one.
 */
export const MIN_FRAMES_FOR_RATE = 3;

/**
 * WHETHER A DELIVERED RATE IS WORTH PUBLISHING YET.
 *
 * `deliveredFps` divides over whatever the window happens to hold, and its arithmetic is correct at every size. What
 * it cannot do is know that the window is not full yet. For the first seconds of a subscription it holds a handful of
 * closely spaced arrivals, and the quotient of a handful is not the delivered rate: it is a reading of the moment the
 * stream opened. That is measured, not supposed. Against a service capped at 4.0 fps this tab published 5.0 at half a
 * second, 4.6 at one second and 4.3 at two, settling to 4.0 only once the window had filled.
 *
 * So a rate is published once the window has been open for its whole length AND enough distinct frames have landed in
 * it. Before that the screen says it is still measuring, which is the true statement. The steady-state calculation is
 * untouched; this decides only whether to show its answer.
 *
 * `firstFrameAtMs` is when the first frame of the CURRENT SOURCE SESSION arrived. A new session restarts the
 * measurement because it restarts the stream: the frames before it came from a different producer, and one rate
 * spanning both would describe neither.
 */
export function fpsReadiness(
  times: readonly number[],
  firstFrameAtMs: number | null,
  nowMs: number,
  windowMs: number = FRAME_WINDOW_MS,
): 'none' | 'measuring' | 'ready' {
  const recent = times.filter((time) => nowMs - time <= windowMs);
  if (recent.length === 0) return 'none'; // nothing in the window: a stopped feed, which is not a warm-up
  if (firstFrameAtMs === null) return 'measuring';
  const openFor = nowMs - firstFrameAtMs;
  return openFor >= windowMs && recent.length >= MIN_FRAMES_FOR_RATE ? 'ready' : 'measuring';
}
