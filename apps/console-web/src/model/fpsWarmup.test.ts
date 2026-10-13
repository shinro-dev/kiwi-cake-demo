// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A RATE IS NOT PUBLISHED UNTIL THE WINDOW HAS FILLED.
//
// Measured against a camera service capped at 4.0 fps, this tab published 5.0 fps half a second after a page load,
// 4.6 at one second and 4.3 at two, settling to 4.0 only once the five-second window had filled. The arithmetic was
// never wrong: `deliveredFps` divides correctly at every sample count. What it could not know is that a handful of
// closely spaced arrivals is a reading of the moment a stream opened rather than the rate it delivers.
//
// These tests pin the three states the screen now has, and the one rule that decides between them.

import { describe, expect, it } from 'vitest';
import { deliveredFps, fpsReadiness, FRAME_WINDOW_MS, MIN_FRAMES_FOR_RATE } from './frameProgression';

/** Arrivals at a steady rate, newest last, as the store keeps them. */
function arrivals(startMs: number, count: number, periodMs: number): number[] {
  return Array.from({ length: count }, (_, index) => startMs + index * periodMs);
}

describe('the rate window has to fill before a number is shown', () => {
  it('is still measuring while the stream has been open for less than the window', () => {
    // Half a second of a 4 fps stream: three arrivals, the count satisfied, the window nowhere near full.
    const times = arrivals(1000, 3, 250);
    const now = 1500;
    expect(fpsReadiness(times, 1000, now)).toBe('measuring');
  });

  it('publishes once the window has been open its whole length and enough frames have landed', () => {
    const times = arrivals(1000, 21, 250); // five seconds of 4 fps
    const now = 6000;
    expect(fpsReadiness(times, 1000, now)).toBe('ready');
    expect(deliveredFps(times, now)).toBeCloseTo(4, 1);
  });

  it('keeps measuring when the window is old enough but too few frames arrived', () => {
    const times = [1000, 5800];
    expect(times.length).toBeLessThan(MIN_FRAMES_FOR_RATE);
    expect(fpsReadiness(times, 1000, 6000)).toBe('measuring');
  });

  it('says nothing rather than measuring when the feed has stopped', () => {
    // Everything has aged out of the window: a stopped feed, which is not a warm-up and must not read as one.
    const times = arrivals(1000, 20, 250);
    const now = 1000 + FRAME_WINDOW_MS * 4;
    expect(fpsReadiness(times, 1000, now)).toBe('none');
    expect(deliveredFps(times, now)).toBeNull();
  });

  it('is measuring when no first-frame time has been recorded yet', () => {
    expect(fpsReadiness([4000], null, 4000)).toBe('measuring');
  });

  it('would have withheld every inflated startup reading that was actually observed', () => {
    // The real sequence, reconstructed from the settling measurement: 0.5s=5.0, 1.0s=4.6, 2.0s=4.3, 3.0s=4.2.
    // Each is above the 4.0 cap and each is now withheld; the 5 s reading is published and is at the cap.
    for (const elapsed of [500, 1000, 2000, 3000, 4000]) {
      const times = arrivals(0, Math.max(1, Math.round((elapsed / 1000) * 4)), 250);
      expect(fpsReadiness(times, 0, elapsed)).toBe('measuring');
    }
    const settled = arrivals(0, 21, 250);
    expect(fpsReadiness(settled, 0, 5000)).toBe('ready');
    expect(deliveredFps(settled, 5000)).toBeCloseTo(4, 1);
  });

  it('leaves the steady-state calculation exactly as it was', () => {
    // Nothing here changes what the estimator computes; only whether its answer is shown.
    const times = arrivals(0, 21, 250);
    expect(deliveredFps(times, 5000)).toBe(4);
    const faster = arrivals(0, 31, 100);
    expect(deliveredFps(faster, 3000)).toBe(10);
  });
});
