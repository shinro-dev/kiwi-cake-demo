// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE ticking source for every age on the screen.
//
// Four panels read the wall clock while they were rendering, which meant an age only changed when something else
// caused a re-render: between the five second snapshot polls every "received 412 ms ago" on the screen stood still and
// then jumped. A frozen age is worse than no age, because it reads as a number that was just measured.
//
// This module ticks once a second and publishes ONE reading. Every panel that draws an age reads the same number in
// the same render, so two panels can never disagree about what time it is.
//
// The reading is the browser's own clock and nothing else. It is used to measure how long ago something reached THIS
// computer, and it is never subtracted from an age measured at the runtime or from a stamp taken on the robot's
// computer: those are other clocks on other machines, and this console never mixes two of them.

/** How often the reading advances. One second: every age on this screen is shown to a tenth of a second at best. */
export const AGE_TICK_MS = 1000;

let current = Date.now();
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function tick(): void {
  current = Date.now();
  for (const listener of listeners) listener();
}

/**
 * The shared reading of the browser's clock.
 *
 * While the ticker is running this is the value taken at the last tick, which is what makes every panel in a render
 * agree. With nobody subscribed there is no ticker, so the reading is taken fresh rather than returned stale.
 */
export function nowMs(): number {
  if (timer === null) current = Date.now();
  return current;
}

/**
 * Subscribe to the tick. The interval exists only while somebody is listening: a tab whose panels have all gone away
 * does not go on waking up once a second.
 */
export function subscribeNow(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    current = Date.now();
    timer = setInterval(tick, AGE_TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** How many readers the tick has. Exported for the test that pins that the interval is shared and then stopped. */
export function nowSubscriberCount(): number {
  return listeners.size;
}
