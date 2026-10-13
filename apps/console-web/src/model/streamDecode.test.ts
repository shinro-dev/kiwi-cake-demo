// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHEN A CAMERA PANEL MAY SAY LIVE, AND WHAT IT DOES WHEN IT MAY NOT.
//
// The operator's ruling: advancing metadata alone does not prove visible video. These pin the state machine the panel
// runs over its own picture element: a decode of THIS attempt is the only way to LIVE; an error or an attempt that
// decodes nothing is failed and retried after a bounded backoff; and a picture that has not changed while the runtime
// keeps reporting new frames is reported as exactly that, never as a stream that ended. Chrome never announces the end
// of a multipart stream (measured: `load` fires once, after the first part, and nothing fires when it ends), and a
// still scene, a covered lens or a dark room can give the same picture over and over while the stream is fine.

import { describe, expect, it } from 'vitest';
import {
  attemptUrl,
  decoded,
  errored,
  elementMounted,
  firstAttempt,
  nextAttempt,
  OPEN_TIMEOUT_MS,
  retryDelayMs,
  retryDue,
  RETRY_DELAYS_MS,
  sampled,
  showingVideo,
  ticked,
  unchangedWhileArrivingMs,
  UNCHANGED_SAMPLES,
} from './streamDecode';

describe('a decode of this attempt is the only way to LIVE', () => {
  it('is not showing video before anything decoded, whatever the runtime says', () => {
    expect(showingVideo(null)).toBe(false);
    expect(showingVideo(firstAttempt(0))).toBe(false);
  });

  it('counts a load only when the element has a width', () => {
    expect(showingVideo(decoded(firstAttempt(0), 0, 10))).toBe(false);
    const shown = decoded(firstAttempt(0), 640, 10);
    expect(showingVideo(shown)).toBe(true);
    expect(shown.decodedAtMs).toBe(10);
  });

  it('never lets an earlier attempt decode stand for a new one', () => {
    const failed = errored(decoded(firstAttempt(0), 640, 10), 20);
    const next = nextAttempt(failed, 5000);
    expect(next.attempt).toBe(2);
    expect(next.phase).toBe('opening');
    expect(next.decodedAtMs).toBeNull();
    expect(showingVideo(next)).toBe(false);
  });
});

describe('an attempt that shows no picture', () => {
  it('is failed on an error, with a reason and a wait before the next attempt', () => {
    const failed = errored(firstAttempt(0), 100);
    expect(failed.phase).toBe('failed');
    expect(failed.why).toContain('did not return a picture');
    expect(failed.retryAtMs).toBe(100 + RETRY_DELAYS_MS[0]!);
    expect(retryDue(failed, 100)).toBe(false);
    expect(retryDue(failed, 100 + RETRY_DELAYS_MS[0]!)).toBe(true);
  });

  it('is failed when nothing decodes within the open timeout', () => {
    expect(ticked(firstAttempt(0), OPEN_TIMEOUT_MS - 1).phase).toBe('opening');
    const late = ticked(firstAttempt(0), OPEN_TIMEOUT_MS);
    expect(late.phase).toBe('failed');
    expect(late.why).toContain('no picture was decoded');
  });

  it('backs off, and the backoff is bounded', () => {
    let state = firstAttempt(0);
    const waits: number[] = [];
    for (let round = 0; round < 10; round += 1) {
      state = errored(state, 0);
      waits.push((state.retryAtMs ?? 0) - 0);
      state = nextAttempt(state, 0);
    }
    expect(waits.slice(0, 6)).toEqual([...RETRY_DELAYS_MS]);
    expect(Math.max(...waits)).toBe(RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1]);
    expect(retryDelayMs(100)).toBe(30_000);
    expect(state.attempt).toBe(11);
  });

  it('starts the backoff over once an attempt decodes', () => {
    const again = nextAttempt(errored(errored(firstAttempt(0), 0), 0), 0);
    expect(again.failures).toBe(1);
    expect(decoded(again, 320, 5).failures).toBe(0);
  });
});

describe('a picture that has not changed is not a stream that ended', () => {
  const live = decoded(firstAttempt(0), 640, 10);

  /** The same digest sampled every 2 s, `count` times, with the runtime's newest frame `frameAgeMs` old each time. */
  function unchanged(from: ReturnType<typeof decoded>, count: number, luminance: number, frameAgeMs: number) {
    let state = from;
    for (let round = 0; round < count; round += 1) state = sampled(state, 'aa', luminance, frameAgeMs, 2000 + round * 2000);
    return state;
  }

  // CHANGED DELIBERATELY. This used to pin that three identical samples while frames kept arriving made the attempt
  // `not_advancing`: the picture was dropped, the chip left LIVE, and a new attempt was made, so a static view flapped
  // LIVE, NOT ADVANCING, LIVE every few seconds. An unchanged picture is not evidence that the stream ended.
  it('keeps the decoded picture, with no retry and no reason to give up, however long it stays the same', () => {
    const state = unchanged(live, 20, 90, 100);
    expect(state.phase).toBe('decoded');
    expect(showingVideo(state)).toBe(true);
    expect(state.retryAtMs).toBeNull();
    expect(state.why).toBeNull();
    expect(state.failures).toBe(0);
    expect(state.attempt).toBe(1);
    expect(retryDue(state, 1_000_000)).toBe(false);
    expect(elementMounted(state)).toBe(true);
  });

  it('measures how long the picture has been the same while the runtime reports new frames', () => {
    // First sample at 2000, then UNCHANGED_SAMPLES identical ones: the same picture from 2000 to 2000 + 2000 * N.
    const state = unchanged(live, UNCHANGED_SAMPLES + 1, 90, 100);
    expect(unchangedWhileArrivingMs(state)).toBe(UNCHANGED_SAMPLES * 2000);
    // One sample short of that, nothing is said yet.
    expect(unchangedWhileArrivingMs(unchanged(live, UNCHANGED_SAMPLES, 90, 100))).toBeNull();
    // A change starts the count over.
    expect(unchangedWhileArrivingMs(sampled(state, 'bb', 90, 100, 60_000))).toBeNull();
  });

  it('says nothing about a still picture when the frames stopped at the source', () => {
    // The metadata says no new frame for 30 s: a camera that stopped, which the chip reports from the metadata.
    const state = unchanged(live, UNCHANGED_SAMPLES + 3, 90, 30_000);
    expect(state.phase).toBe('decoded');
    expect(unchangedWhileArrivingMs(state)).toBeNull();
  });

  it('keeps a nearly black picture as a decoded picture and reports how dark it is', () => {
    const state = unchanged(live, 20, 2, 100);
    expect(state.phase).toBe('decoded');
    expect(showingVideo(state)).toBe(true);
    expect(state.luminance).toBe(2);
    expect(elementMounted(state)).toBe(true);
  });

  it('keeps LIVE while the picture changes, and never judges a sample that could not be read', () => {
    let state = live;
    for (let round = 0; round < 8; round += 1) state = sampled(state, `digest-${String(round)}`, 90, 100, 2000 + round * 2000);
    expect(state.phase).toBe('decoded');
    expect(unchangedWhileArrivingMs(state)).toBeNull();
    for (let round = 0; round < 8; round += 1) state = sampled(state, null, null, 100, 20_000 + round * 2000);
    expect(state.phase).toBe('decoded');
  });
});

describe('a stream that broke is a truthful non-live state', () => {
  it('drops the picture and retries after the bounded backoff when the element errors after a decode', () => {
    const broke = errored(decoded(firstAttempt(0), 640, 10), 5000);
    expect(showingVideo(broke)).toBe(false);
    expect(elementMounted(broke)).toBe(false);
    expect(broke.why).toContain('broke');
    expect(broke.retryAtMs).toBe(5000 + RETRY_DELAYS_MS[0]!);
  });
});

describe('each attempt is its own request', () => {
  it('carries the attempt number beside the role, which the API ignores', () => {
    expect(attemptUrl('/api/preview.mjpg?role=wrist', 3)).toBe('/api/preview.mjpg?role=wrist&attempt=3');
    expect(attemptUrl('/api/preview.mjpg', 1)).toBe('/api/preview.mjpg?attempt=1');
  });
});
