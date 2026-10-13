// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// "There is no picture" is not one condition. A camera that was never configured, a camera the runtime is not holding
// open, and a camera whose frames stopped arriving are three different facts about the robot, and an operator who is
// told the wrong one goes and does the wrong thing about it. These tests pin that the three stay apart, for both
// sources, and that nothing on the screen carries a number that nothing reported.

import { describe, expect, it } from 'vitest';
import type { ClockDomain, PreviewSlot, TapSlot } from '@kiwi/demo-contracts';
import type { PreviewSample, TapSample } from '../api/types';
import { previewView, SILENT_AFTER_MS, tapView } from './sources';

const NOW = 1_000_000;

function tap(extra: Partial<TapSample>): TapSample {
  return {
    source_stamp: null,
    joints: null,
    base_velocity: null,
    receive_age_ms: null,
    subscriber_gap_count: null,
    publisher_drop_count: null,
    ...extra,
  };
}

function preview(extra: Partial<PreviewSample>): PreviewSample {
  return { role: 'wrist', seq: null, capture_clock: null, receive_age_ms: null, measured_fps: null, available: false, reason: null, ...extra };
}

describe('the three unavailable states of the tap', () => {
  it('reports NOT ATTACHED when nothing is configured, with the server reason', () => {
    const view = tapView(tap({ state: 'disconnected', reason: 'tap_not_configured' }), NOW, NOW);
    expect(view.availability).toBe('not_attached');
    expect(view.reason).toBe('tap_not_configured');
    expect(view.server_state).toBe('disconnected');
    expect(view.live).toBe(false);
  });

  it('reports DISCONNECTED when a source exists and is not held open, with the server reason', () => {
    const view = tapView(tap({ state: 'disconnected', reason: 'stopped' }), NOW, NOW);
    expect(view.availability).toBe('disconnected');
    expect(view.reason).toBe('stopped');
  });

  it('reports STALLED and keeps the LAST stamp while refusing to show its values', () => {
    const view = tapView(
      tap({
        state: 'stalled',
        reason: 'no_sample_within_the_fresh_window',
        source_stamp: { session: 'host-session-a', seq: 991, sample_mono_ns: 42, clock_domain: 'pi_monotonic' },
        receive_age_ms: 7400,
        // The runtime nulls the values on a stalled tap. Nothing here puts them back.
        joints: null,
        base_velocity: null,
      }),
      NOW,
      NOW,
    );
    expect(view.availability).toBe('stalled');
    expect(view.reason).toBe('no_sample_within_the_fresh_window');
    expect(view.source_stamp?.seq).toBe(991);
    expect(view.receive_age_ms).toBe(7400);
    expect(view.joints).toBeNull();
    expect(view.live).toBe(false);
  });

  it('keeps all three apart rather than collapsing them into one word', () => {
    const states = [
      tapView(tap({ state: 'disconnected', reason: 'tap_not_configured' }), NOW, NOW).availability,
      tapView(tap({ state: 'disconnected', reason: 'open_failed' }), NOW, NOW).availability,
      tapView(tap({ state: 'stalled', reason: 'no_sample_within_the_fresh_window' }), NOW, NOW).availability,
    ];
    expect(new Set(states).size).toBe(3);
  });
});

describe('the three unavailable states of the preview', () => {
  it('reports NOT ATTACHED when no camera source is configured', () => {
    const view = previewView(preview({ state: 'disconnected', reason: 'preview_not_configured' }), NOW, NOW);
    expect(view.availability).toBe('not_attached');
    expect(view.reason).toBe('preview_not_configured');
    expect(view.streamable).toBe(false);
  });

  it('reports DISCONNECTED with the server reason and offers no picture', () => {
    const view = previewView(preview({ state: 'disconnected', reason: 'subscriber_closed' }), NOW, NOW);
    expect(view.availability).toBe('disconnected');
    expect(view.streamable).toBe(false);
  });

  it('reports STALLED and never lets a stalled source be drawn as a moving stream', () => {
    const view = previewView(preview({ state: 'stalled', reason: 'no_frame_within_the_fresh_window', seq: 12, available: false }), NOW, NOW);
    expect(view.availability).toBe('stalled');
    expect(view.streamable).toBe(false);
    expect(view.measured_fps).toBeNull();
  });

  it('keeps all three apart', () => {
    const states = [
      previewView(preview({ state: 'disconnected', reason: 'preview_source_not_attached' }), NOW, NOW).availability,
      previewView(preview({ state: 'disconnected', reason: 'open_failed' }), NOW, NOW).availability,
      previewView(preview({ state: 'stalled', reason: 'no_frame_within_the_fresh_window' }), NOW, NOW).availability,
    ];
    expect(new Set(states).size).toBe(3);
  });

  it('separates CONNECTING from both of them: the socket is open and no frame has come through it', () => {
    const view = previewView(preview({ state: 'connecting', reason: 'no_frame_yet' }), NOW, NOW);
    expect(view.availability).toBe('connecting');
    expect(view.streamable).toBe(false);
  });
});

describe('numbers nobody reported', () => {
  it('leaves every measured field null on an unattached tap', () => {
    const view = tapView(tap({ state: 'disconnected', reason: 'tap_not_configured' }), null, NOW);
    for (const value of [view.receive_age_ms, view.measured_rate_hz, view.subscriber_gap_count, view.publisher_drop_count, view.session_changes]) {
      expect(value).toBeNull();
    }
    expect(view.silent_for_ms).toBeNull();
  });

  it('leaves the preview capture age null and carries the server reason for why it cannot be computed', () => {
    const view = previewView(
      preview({
        state: 'connected',
        available: true,
        capture_age_ms: null,
        capture_age_reason: 'no_clock_relation_between_source_and_laptop',
        service_latency_ms: 12.5,
        capture_clock: { session: 'camera-session-a', seq: 40, sample_mono_ns: 99, clock_domain: 'pi_monotonic' },
      }),
      NOW,
      NOW,
    );
    expect(view.capture_age_ms).toBeNull();
    expect(view.capture_age_reason).toBe('no_clock_relation_between_source_and_laptop');
    // The one span inside a single clock IS reported, and it is the only one.
    expect(view.service_latency_ms).toBe(12.5);
    expect(view.capture_clock_domain).toBe('pi_monotonic');
  });

  it('refuses a value that is not a finite number rather than printing it', () => {
    const view = tapView(tap({ state: 'connected', receive_age_ms: Number.NaN, measured_rate_hz: Number.POSITIVE_INFINITY }), NOW, NOW);
    expect(view.receive_age_ms).toBeNull();
    expect(view.measured_rate_hz).toBeNull();
  });

  it('names the two clock domains and never merges them', () => {
    const view = tapView(
      tap({
        state: 'connected',
        source_stamp: { session: 'host-session-a', seq: 7, sample_mono_ns: 5, clock_domain: 'pi_monotonic' },
        clock_domains: { sample: 'pi_monotonic', receive: 'laptop_monotonic' },
        receive_age_ms: 31,
      }),
      NOW,
      NOW,
    );
    expect(view.sample_clock_domain).toBe('pi_monotonic');
    expect(view.receive_clock_domain).toBe('laptop_monotonic');
  });
});

describe("what this browser knows on its own clock", () => {
  it('calls a connected source SILENT once its lines stop reaching this tab, and labels that as this tab', () => {
    const sample = tap({ state: 'connected', receive_age_ms: 30 });
    expect(tapView(sample, NOW - 100, NOW).availability).toBe('connected');
    const quiet = tapView(sample, NOW - (SILENT_AFTER_MS + 1), NOW);
    expect(quiet.availability).toBe('silent');
    expect(quiet.live).toBe(false);
    // The runtime still says connected, and that is still reported as the server's own word.
    expect(quiet.server_state).toBe('connected');
    expect(quiet.silent_for_ms).toBe(SILENT_AFTER_MS + 1);
  });

  it('will not stream a preview the runtime calls available once it stops reaching this tab', () => {
    const sample = preview({ state: 'connected', available: true, measured_fps: 8.1 });
    expect(previewView(sample, NOW - 100, NOW).streamable).toBe(true);
    expect(previewView(sample, NOW - (SILENT_AFTER_MS + 1), NOW).streamable).toBe(false);
  });

  it('says no line has arrived at all rather than calling it a disconnection', () => {
    const view = tapView(null, null, NOW);
    expect(view.availability).toBe('never_arrived');
    expect(view.reason).toBeNull();
    expect(view.server_state).toBeNull();
  });
});

describe('a source that does not say what state it is in', () => {
  it('is reported as unstated and is never assumed to be connected', () => {
    const view = previewView(preview({ available: false, reason: 'something a newer runtime says' }), NOW, NOW);
    expect(view.availability).toBe('unstated');
    expect(view.reason).toBe('something a newer runtime says');
    expect(view.streamable).toBe(false);
  });

  it('is reported as unstated even when it says available, because the state is what the screen routes on', () => {
    const view = previewView(preview({ available: true }), NOW, NOW);
    expect(view.availability).toBe('unstated');
    // It still streams: `available` is the only thing that source stated and it is taken at its word for the picture.
    expect(view.streamable).toBe(true);
  });

  it('treats a state word this console does not know as unstated, never as a guess at the nearest one', () => {
    const view = tapView(tap({ state: 'quiescing', reason: 'a_newer_runtime' }), NOW, NOW);
    expect(view.availability).toBe('unstated');
    expect(view.server_state).toBe('quiescing');
  });
});

describe('the age of a picture, which spans two machines', () => {
  const CAPTURE = { session: 'camera-session-a', seq: 40, sample_mono_ns: 99, clock_domain: 'pi_monotonic' as const };
  const RELATION = {
    domains: ['pi_monotonic', 'laptop_monotonic'] as [ClockDomain, ClockDomain],
    offset_ns: 1_000_000,
    uncertainty_ns: 250_000,
    method: 'round_trip_probe',
    measured_at: null,
  };

  it('refuses a capture age a runtime reports with no measured clock relation behind it', () => {
    const view = previewView(preview({ state: 'connected', available: true, capture_clock: CAPTURE, capture_age_ms: 44 }), NOW, NOW);
    expect(view.capture_age_ms).toBeNull();
    expect(view.capture_age_reason).toBe('capture_age_without_a_measured_clock_relation');
    expect(view.clock_relation).toBeNull();
  });

  it('allows one when a relation between those two clocks has been measured, and says what it rests on', () => {
    const view = previewView(
      preview({ state: 'connected', available: true, capture_clock: CAPTURE, capture_age_ms: 44, clock_relation: RELATION }),
      NOW,
      NOW,
    );
    expect(view.capture_age_ms).toBe(44);
    expect(view.clock_relation?.uncertainty_ns).toBe(250_000);
  });

  it('refuses one licensed by a relation between two OTHER clocks', () => {
    const elsewhere = { ...RELATION, domains: ['wall_utc', 'laptop_monotonic'] as [ClockDomain, ClockDomain] };
    const view = previewView(
      preview({ state: 'connected', available: true, capture_clock: CAPTURE, capture_age_ms: 44, clock_relation: elsewhere }),
      NOW,
      NOW,
    );
    expect(view.capture_age_ms).toBeNull();
    expect(view.capture_age_reason).toBe('capture_age_without_a_measured_clock_relation');
  });

  it('keeps the runtime s own reason when it reported no age at all, and goes on reporting the receive age', () => {
    const view = previewView(
      preview({
        state: 'connected',
        available: true,
        capture_clock: CAPTURE,
        capture_age_ms: null,
        capture_age_reason: 'no_clock_relation_between_source_and_laptop',
        receive_age_ms: 31,
      }),
      NOW,
      NOW,
    );
    expect(view.capture_age_ms).toBeNull();
    expect(view.capture_age_reason).toBe('no_clock_relation_between_source_and_laptop');
    // The receive age is measured in one clock and is always available. It is not a stand-in for the capture age.
    expect(view.receive_age_ms).toBe(31);
  });
});

describe('a preview that stood aside for a decision capture', () => {
  it('is counted as yielding, which is not a stall and must not be read as one', () => {
    const view = previewView(preview({ state: 'connected', available: true, skipped_for_mission: 6, capture_time_stalls: 0 }), NOW, NOW);
    expect(view.skipped_for_mission).toBe(6);
    expect(view.capture_time_stalls).toBe(0);
    expect(view.availability).toBe('connected');
  });

  it('reports nothing rather than zero when the source does not count them', () => {
    expect(previewView(preview({ state: 'connected', available: true }), NOW, NOW).skipped_for_mission).toBeNull();
  });
});

describe('the shapes this console reads a source slot with', () => {
  // These are compile-time statements. They fail the typecheck, not the assertion, and that is the point: the console
  // must not carry a field the contract does not have, because a field nobody sends can only ever read as null.
  type OnlyContractFields<Surface, Slot> = Exclude<keyof Surface, keyof Slot> extends never ? true : never;
  const tapCarriesOnlyContractFields: OnlyContractFields<TapSample, TapSlot> = true;
  const previewCarriesOnlyContractFields: OnlyContractFields<PreviewSample, PreviewSlot> = true;

  it('carry no field the contract does not define', () => {
    expect(tapCarriesOnlyContractFields).toBe(true);
    expect(previewCarriesOnlyContractFields).toBe(true);
  });

  it('read a whole published tap slot, with the one widening this console states', () => {
    const slot: TapSlot = {
      available: true,
      state: 'connected',
      reason: null,
      source_stamp: { session: 'host-session-a', seq: 7, sample_mono_ns: 5, clock_domain: 'pi_monotonic' },
      joints: { shoulder_pan: 0.1, shoulder_lift: null, elbow_flex: null, wrist_flex: null, wrist_roll: null, gripper: null },
      base_velocity: { x_mps: null, y_mps: null, theta_dps: null },
      receive_mono_ns: 12,
      receive_age_ms: 31,
      clock_domains: { sample: 'pi_monotonic', receive: 'laptop_monotonic' },
      measured_rate_hz: 9.8,
      subscriber_gap_count: 0,
      publisher_drop_count: 0,
      session_changes: 0,
      malformed_count: 0,
      transport_errors: 0,
      lease: null,
      rejections: null,
      commands: null,
      ingestion: null,
    };
    // The widening: this console prints whatever joints ARRIVED, by name, rather than the six the contract names, so
    // a host that reports another one is visible instead of being dropped by a type.
    const sample: TapSample = { ...slot, joints: slot.joints === null ? null : { ...slot.joints } };
    const view = tapView(sample, NOW, NOW);
    expect(view.availability).toBe('connected');
    expect(view.joints?.['shoulder_pan']).toBe(0.1);
    expect(view.measured_rate_hz).toBe(9.8);
  });
});

// THE FRAME-PROGRESSION COUNTERS, END TO END.
//
// The runtime publishes `gaps` as the number of gap events and `malformed` as a total, both `integer|null`. It used
// to publish the preview tracker's own objects instead, so `finite()` turned every one of them into null and the only
// frame-progression fault signal the runtime computes reached nothing. Producer side is pinned in
// packages/demo-runtime/tests/test_sources.py; this is the browser end of the same path.
describe('the frame-progression counters a preview slot carries', () => {
  it('carries an integer counter through to the view', () => {
    const view = previewView(preview({ gaps: 3, session_changes: 1, capture_time_stalls: 2, malformed: 7 }), NOW, NOW);
    expect(view.gaps).toBe(3);
    expect(view.session_changes).toBe(1);
    expect(view.capture_time_stalls).toBe(2);
    expect(view.malformed).toBe(7);
  });

  it('keeps a real zero apart from a counter nobody kept', () => {
    expect(previewView(preview({ gaps: 0 }), NOW, NOW).gaps).toBe(0);
    expect(previewView(preview({ gaps: null }), NOW, NOW).gaps).toBeNull();
  });

  it('refuses the old object shape rather than reading a number out of it', () => {
    // If the producer ever regresses, the screen must say unavailable and never invent a count.
    const view = previewView(preview({ gaps: { events: 1, missing_previews: 3 } as unknown as number }), NOW, NOW);
    expect(view.gaps).toBeNull();
  });
});
