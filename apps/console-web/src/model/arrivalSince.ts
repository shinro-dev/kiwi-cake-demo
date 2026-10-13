// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHEN A RUN OF ARRIVALS BEGAN, in this browser's clock (spec 6.8; browser r2 N4, verifier R-2).
//
// A feed that keeps arriving (the telemetry tap, the event stream, the snapshot poll) is said as a rate and the fixed
// time its current run of arrivals began. The time of the newest sample moves with every sample and made Diagnostics
// change every second at a quiet end state; the start of the run moves only when the feed goes silent and comes back.

/**
 * The start of the current run of arrivals after one more arrival at `atMs`: kept while the arrival follows the one
 * before it (`lastAtMs`) within `gapMs`, restarted at `atMs` after a longer silence or when there was none before.
 */
export function arrivalSince(since: number | null, lastAtMs: number | null, atMs: number, gapMs: number): number {
  return since !== null && lastAtMs !== null && atMs - lastAtMs <= gapMs ? since : atMs;
}

/** A snapshot older than this is not current: the Connections tab's own threshold, stated with its words. */
export const SNAPSHOT_CURRENT_WITHIN_MS = 15_000;
