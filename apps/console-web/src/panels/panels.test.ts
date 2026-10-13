// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Two rules that a running API proved wrong, pinned here so they cannot come back. Both were found by loading the BUILT
// page in a browser against a real console-api in front of a real runtime daemon in --synthetic mode: neither is
// reachable from a unit test that talks to an injected source, because both are about what the runtime really sends.

import { describe, expect, it } from 'vitest';
import { telemetrySilentForMs } from './LiveViewPanel';
import { modeBanner } from './StatusStrip';

describe('when the live view may say the feed is silent', () => {
  it('counts the silence from the last telemetry line of ANY kind, not from the preview alone', () => {
    // What a launch really looks like: the runtime sends the preview slot's defaults once, when the subscriber
    // attaches, and never again because no preview source is attached; tap lines keep arriving every 100 ms.
    const telemetry = { preview_received_at_ms: 1_000, tap_received_at_ms: 40_000 };
    expect(telemetrySilentForMs(telemetry, 40_050)).toBe(50);
    // Reading the preview arrival alone gave 39 050 ms here, and the panel then said "No telemetry sample has arrived
    // for 39.05 s" over a stream that had delivered a sample 50 ms earlier.
  });

  it('is silent when nothing arrives at all, whichever slot stopped', () => {
    expect(telemetrySilentForMs({ preview_received_at_ms: 1_000, tap_received_at_ms: 2_000 }, 60_000)).toBe(58_000);
  });

  it('says nothing rather than zero before any telemetry has arrived', () => {
    expect(telemetrySilentForMs({ preview_received_at_ms: null, tap_received_at_ms: null }, 10_000)).toBeNull();
  });

  it('still counts from the preview when that is the only line that ever arrived', () => {
    expect(telemetrySilentForMs({ preview_received_at_ms: 5_000, tap_received_at_ms: null }, 9_000)).toBe(4_000);
  });
});

describe('the banner that says what the screen is', () => {
  it('does not tell an operator that no runtime was involved when a runtime produced the record', () => {
    const text = modeBanner('synthetic', 'live');
    expect(text).not.toBeNull();
    expect(text).toContain('a real runtime and a real controller process');
    expect(text).not.toContain('no runtime');
  });

  it('says nothing outside the browser was involved only for the script inside the page', () => {
    expect(modeBanner('synthetic', 'synthetic')).toContain('No runtime, no robot and no camera were involved');
  });

  it('names a replay as a recording whichever source it came from', () => {
    expect(modeBanner('replay', 'live')).toContain('recorded run being replayed');
    expect(modeBanner('replay', 'synthetic')).toContain('recorded run being replayed');
  });

  it('says nothing at all about a live runtime', () => {
    expect(modeBanner('live', 'live')).toBeNull();
  });
});
