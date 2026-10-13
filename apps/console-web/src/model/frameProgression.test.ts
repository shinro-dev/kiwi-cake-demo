// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The cases these rules exist for are the ones a real feed produces: a heartbeat while stalled, a duplicated line, a
// line that overtook another, a service restart that renumbers from zero, and a motionless scene whose frames are
// byte-identical and are still new captures.

import { describe, expect, it } from 'vitest';
import type { PreviewSample } from '../api/types';
import { deliveredFps, frameIdentityOf, FRAME_WINDOW_MS, isNewFrame, withFrameAt } from './frameProgression';

const SESSION = 'cs-20260922T173941Z-fbfe6699';

function sample(over: Record<string, unknown> = {}): PreviewSample {
  return {
    available: true,
    reason: null,
    role: 'wrist',
    seq: 10,
    source_seq: 110,
    capture_clock: { session: SESSION, seq: 10, sample_mono_ns: 1, clock_domain: 'pi_monotonic' },
    receive_age_ms: 12,
    measured_fps: 30,
    ...over,
  } as unknown as PreviewSample;
}

describe('the identity of a delivered frame', () => {
  it('reads the session and both counters from a delivering slot', () => {
    expect(frameIdentityOf(sample())).toEqual({ session: SESSION, seq: 10, sourceSeq: 110 });
  });

  it('is nothing at all for a slot that is not delivering, however much it still carries', () => {
    // The STALLED heartbeat: the runtime keeps answering and keeps quoting where the feed stopped.
    const stalled = sample({ available: false, reason: 'no_sample_within_the_fresh_window', seq: 10, capture_clock: null });
    expect(frameIdentityOf(stalled)).toBeNull();
  });

  it('is nothing for a slot that carries no number anybody could compare', () => {
    expect(frameIdentityOf(sample({ seq: null, source_seq: null, capture_clock: null }))).toBeNull();
    expect(frameIdentityOf(null)).toBeNull();
    expect(frameIdentityOf(undefined)).toBeNull();
  });
});

describe('whether a line carried a frame this tab has not counted', () => {
  const at = (seq: number, session: string = SESSION) => frameIdentityOf(sample({ seq, source_seq: seq + 100, capture_clock: { session, seq } }));

  it('counts the first identifiable frame', () => {
    expect(isNewFrame(null, at(1))).toBe(true);
  });

  it('does not count a line that delivered no frame', () => {
    expect(isNewFrame(at(10), null)).toBe(false);
    // The exact stalled-heartbeat case, end to end: it must not move the age.
    const heartbeat = frameIdentityOf(sample({ available: false, seq: 10, capture_clock: null }));
    expect(isNewFrame(at(10), heartbeat)).toBe(false);
  });

  it('does not count a repeat or an out-of-order line', () => {
    expect(isNewFrame(at(10), at(10))).toBe(false);
    expect(isNewFrame(at(10), at(9))).toBe(false);
  });

  it('counts a frame whose sequence advanced, including across a jump', () => {
    expect(isNewFrame(at(10), at(11))).toBe(true);
    expect(isNewFrame(at(10), at(57))).toBe(true); // frames were missed; the ones that arrived are still new
  });

  it('counts the first frame of a new session even though it renumbers from zero', () => {
    const restarted = at(0, 'cs-20260923T090000Z-aaaabbbb');
    expect(isNewFrame(at(4211), restarted)).toBe(true);
  });

  it('does not read one unknown session as a restart', () => {
    const unknownSession = frameIdentityOf(sample({ seq: 9, source_seq: 109, capture_clock: null }));
    expect(isNewFrame(at(10), unknownSession)).toBe(false);
    expect(isNewFrame(at(10), frameIdentityOf(sample({ seq: 11, source_seq: 111, capture_clock: null })))).toBe(true);
  });

  it("falls back to the camera's own frame number when the preview counter is absent", () => {
    const a = frameIdentityOf(sample({ seq: null, source_seq: 500 }));
    const b = frameIdentityOf(sample({ seq: null, source_seq: 501 }));
    expect(isNewFrame(a, b)).toBe(true);
    expect(isNewFrame(b, a)).toBe(false);
  });

  it('claims nothing when one side carries a number the other does not', () => {
    const withSeq = frameIdentityOf(sample({ seq: 10, source_seq: null, capture_clock: null }));
    const withSourceSeq = frameIdentityOf(sample({ seq: null, source_seq: 900, capture_clock: null }));
    expect(isNewFrame(withSeq, withSourceSeq)).toBe(false);
  });

  it('never looks at the picture, so a motionless scene still delivers new frames', () => {
    // Two frames of an unchanging scene: same size, same everything a reader could compare except the numbers.
    const still = (seq: number) => frameIdentityOf(sample({ seq, source_seq: seq + 100, capture_clock: { session: SESSION, seq }, frame: { width_px: 1280, height_px: 720 } }));
    expect(isNewFrame(still(10), still(11))).toBe(true);
  });
});

describe('the delivered-frame rate this tab saw', () => {
  it('is null until two frames have arrived in the window', () => {
    expect(deliveredFps([], 1000)).toBeNull();
    expect(deliveredFps([1000], 1000)).toBeNull();
  });

  it('measures over the arrivals inside the window', () => {
    // Eleven arrivals 100 ms apart: ten intervals over one second.
    const times = Array.from({ length: 11 }, (_, index) => 10_000 + index * 100);
    expect(deliveredFps(times, 11_000)).toBe(10);
  });

  it('falls away rather than reporting the rate it used to have', () => {
    const times = [1000, 1100, 1200];
    expect(deliveredFps(times, 1200)).toBeCloseTo(10, 3);
    // The feed stopped and the window has moved past every arrival.
    expect(deliveredFps(times, 1200 + FRAME_WINDOW_MS + 1)).toBeNull();
  });

  it('keeps only the arrivals inside the window as they are added', () => {
    let times: number[] = [];
    times = withFrameAt(times, 0);
    times = withFrameAt(times, 100);
    times = withFrameAt(times, 200);
    expect(times).toEqual([0, 100, 200]);
    times = withFrameAt(times, FRAME_WINDOW_MS + 150);
    expect(times).toEqual([200, FRAME_WINDOW_MS + 150]);
  });

  it('is bounded, so a long-lived tab does not grow a list forever', () => {
    let times: number[] = [];
    for (let index = 0; index < 1000; index += 1) times = withFrameAt(times, index);
    expect(times.length).toBeLessThanOrEqual(256);
  });
});

// THE POWER CYCLE. The laptop runtime keeps running and its session never changes; the camera on the robot goes away
// and comes back with a NEW camera-service session numbering from zero. The three things that must not happen: an
// announcement of the new session must not refresh the frame age, it must not make the source read as delivering, and
// the first real frame of that new session must still count even though its seq is lower than the old session's.
describe('a camera that disappears and comes back with a new session', () => {
  const session = (name: string, seq: number, available = true) =>
    frameIdentityOf(sample({ available, seq, source_seq: seq + 100, capture_clock: { session: name, seq } }));
  const OLD = 'cs-20260922T173941Z-fbfe6699';
  const NEW = 'cs-20260923T090000Z-aaaabbbb';

  it('does not count the new session being announced while no frame has arrived', () => {
    const last = session(OLD, 4211);
    // CONNECTING: the runtime names the new session and has no picture yet.
    const announced = frameIdentityOf(sample({ available: false, reason: 'no_frame_yet', seq: null, source_seq: null, capture_clock: { session: NEW, seq: null } }));
    expect(announced).toBeNull();
    expect(isNewFrame(last, announced)).toBe(false);
  });

  it('does not count a slot that carries the new session and still says it is not delivering', () => {
    const last = session(OLD, 4211);
    expect(isNewFrame(last, session(NEW, 0, false))).toBe(false);
    expect(isNewFrame(last, session(NEW, 7, false))).toBe(false);
  });

  it('counts the first REAL frame of the new session, whose seq is lower than the old one', () => {
    const last = session(OLD, 4211);
    expect(isNewFrame(last, session(NEW, 0))).toBe(true);
    expect(isNewFrame(last, session(NEW, 1))).toBe(true);
  });

  it('goes on counting inside the new session and refuses its repeats', () => {
    expect(isNewFrame(session(NEW, 0), session(NEW, 1))).toBe(true);
    expect(isNewFrame(session(NEW, 1), session(NEW, 1))).toBe(false);
    expect(isNewFrame(session(NEW, 1), session(NEW, 0))).toBe(false);
  });

  it('lets the delivered rate fall away across the gap rather than carrying the old rate over', () => {
    // Frames up to the moment the camera went away, then nothing for longer than the window.
    const before = [1000, 1100, 1200];
    expect(deliveredFps(before, 1200)).toBeCloseTo(10, 3);
    expect(deliveredFps(before, 1200 + FRAME_WINDOW_MS + 1)).toBeNull();
    // One frame from the new session is still not two: a rate is not claimed from a single arrival.
    const after = withFrameAt(before, 1200 + FRAME_WINDOW_MS + 2);
    expect(after).toEqual([1200 + FRAME_WINDOW_MS + 2]);
    expect(deliveredFps(after, 1200 + FRAME_WINDOW_MS + 2)).toBeNull();
  });
});
