// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The telemetry tap and the camera preview, read as the runtime states them.
//
// The point of this file is that "there is no data" is not one condition but several, and they are not the same fact:
//
//   NOT ATTACHED  nothing was ever configured to attach to. There is no source, so there is nothing to reconnect to.
//   DISCONNECTED  a source is configured and this runtime is not holding it open, with the runtime's reason.
//   CONNECTING    the socket is open and no sample has come through it yet.
//   STALLED       samples arrived and then stopped. The LAST stamp and how long ago it came are still facts; the
//                 values in it are not current and are not shown.
//   FAILED        the transport itself broke, with the runtime's reason.
//   SILENT        the runtime's own line has stopped reaching this BROWSER. That is this console's measurement, on
//                 this computer's clock, and it is labelled as such and never mixed with the runtime's ages.
//   UNSTATED      a line arrived from something that did not say what state it is in. It is reported as unstated.
//
// Two rules run through everything here. A number that nothing reported is null and the screen prints the word
// unavailable for it. And the sample clock and the receive clock are never subtracted from each other: the runtime
// names both domains, and a preview's capture age is null with the runtime's reason for why it cannot be computed.

import type { ClockDomains, PreviewSample, TapSample } from '../api/types';
import type { ClockRelation, MeasuredBaseVelocity, SourceStamp, TapLease } from '@kiwi/demo-contracts';

export type SourceAvailability =
  | 'connected'
  | 'connecting'
  | 'stalled'
  | 'disconnected'
  | 'not_attached'
  | 'failed'
  | 'silent'
  | 'never_arrived'
  | 'unstated';

/**
 * How long this console waits for another line of a given slot before it stops presenting the last one as current.
 * Measured in the BROWSER's clock, on arrivals, and never subtracted from any age inside a sample.
 */
export const SILENT_AFTER_MS = 4000;

/**
 * The runtime's reason codes that mean "no source was configured". Taken verbatim from `demo_runtime.sources`; a reason
 * outside this set is a disconnection, not an absence, and the two are shown differently.
 */
const NOT_ATTACHED_REASONS: ReadonlySet<string> = new Set([
  'tap_not_configured',
  'tap_source_not_attached',
  'preview_not_configured',
  'preview_source_not_attached',
]);

export interface SourceStatus {
  availability: SourceAvailability;
  /** The runtime's own state word, exactly as it arrived, or null when the sample did not carry one. */
  server_state: string | null;
  /** The runtime's reason code, exactly as it arrived. Null when the runtime stated none. */
  reason: string | null;
  /** True only when the runtime says it has a live source AND a line has reached this browser recently. */
  live: boolean;
  /** How long this slot has been silent in the browser's clock, or null when no line of it ever arrived. */
  silent_for_ms: number | null;
}

/**
 * How long this slot has been quiet, in the browser's clock.
 *
 * IT IS NEVER NEGATIVE. `nowMs` is the shared tick, read once a second, and an arrival stamp is taken the instant the
 * line lands, so a line that arrives between two ticks is stamped AFTER the reading this render is using and the
 * subtraction comes out below zero. A negative age is not a fact about anything, and printed on a camera card it reads
 * as "last line -325 ms ago", which is how this was found. The honest reading of it is that the line just arrived.
 *
 * `model/steps.ts` clamps `telemetryAgeMs` for exactly this reason and says so; this is the same rule, applied where
 * every source slot passes through rather than in one of the panels that reads one.
 */
function silence(receivedAtMs: number | null, nowMs: number): number | null {
  return receivedAtMs === null ? null : Math.max(0, nowMs - receivedAtMs);
}

/**
 * The one place a state word becomes an availability. `sample === null` is "no line of this slot has arrived", which is
 * not the same as a source that said it is disconnected, and neither is the same as no source at all.
 */
function statusOf(
  sample: { state?: string | null; reason?: string | null; available?: boolean | null } | null,
  receivedAtMs: number | null,
  nowMs: number,
  silentAfterMs: number,
): SourceStatus {
  const silentFor = silence(receivedAtMs, nowMs);
  if (sample === null) {
    return { availability: 'never_arrived', server_state: null, reason: null, live: false, silent_for_ms: silentFor };
  }
  const state = typeof sample.state === 'string' ? sample.state : null;
  const reason = typeof sample.reason === 'string' ? sample.reason : null;
  const base = { server_state: state, reason, silent_for_ms: silentFor };

  if (state === null) {
    // Something answered without saying what state it is in. `available` is the only other thing it stated, and it is
    // reported as exactly that: unstated, with whatever reason came with it.
    if (sample.available === true) {
      const quiet = silentFor !== null && silentFor > silentAfterMs;
      return { ...base, availability: quiet ? 'silent' : 'unstated', live: !quiet };
    }
    return { ...base, availability: 'unstated', live: false };
  }
  if (state === 'connected') {
    const quiet = silentFor !== null && silentFor > silentAfterMs;
    return { ...base, availability: quiet ? 'silent' : 'connected', live: !quiet };
  }
  if (state === 'stalled') return { ...base, availability: 'stalled', live: false };
  if (state === 'connecting') return { ...base, availability: 'connecting', live: false };
  if (state === 'failed') return { ...base, availability: 'failed', live: false };
  if (state === 'disconnected') {
    return { ...base, availability: reason !== null && NOT_ATTACHED_REASONS.has(reason) ? 'not_attached' : 'disconnected', live: false };
  }
  return { ...base, availability: 'unstated', live: false };
}

/** A sentence for each availability. The runtime's reason is appended by the panel, never replaced by this. */
const AVAILABILITY_SENTENCE: Record<SourceAvailability, string> = {
  connected: 'lines are arriving and the runtime holds the source open',
  connecting: 'the runtime has the socket open and no sample has come through it yet',
  stalled: 'samples arrived and then stopped: the last stamp is kept, its values are not shown as current',
  disconnected: 'the runtime is not holding this source open',
  not_attached: 'no source of this kind is configured on this runtime: there is nothing to connect to',
  failed: 'the transport itself broke',
  silent: "the runtime says it is connected, but no line has reached this browser recently, on this computer's clock",
  never_arrived: 'no line of this kind has reached this browser at all',
  unstated: 'what arrived did not say what state the source is in',
};

export function availabilitySentence(availability: SourceAvailability): string {
  return AVAILABILITY_SENTENCE[availability];
}

export interface TapView extends SourceStatus {
  /** The host's own session and sequence number, as it stamped them. Null when no sample carried one. */
  source_stamp: SourceStamp | null;
  /** The sample's clock domain, straight from the stamp. The age below is NOT in this domain. */
  sample_clock_domain: string | null;
  /** The laptop's clock domain for `receive_age_ms`, as the runtime named it. */
  receive_clock_domain: string | null;
  clock_domains: ClockDomains | null;
  /** How long ago the runtime received the last sample, in the RECEIVE clock. Null when nothing reported it. */
  receive_age_ms: number | null;
  /** Measured at the runtime, over a window of arrivals. Null when it reported none. */
  measured_rate_hz: number | null;
  subscriber_gap_count: number | null;
  publisher_drop_count: number | null;
  session_changes: number | null;
  malformed_count: number | null;
  transport_errors: number | null;
  /** Values only while connected. A stalled tap reports null joints, which is what the runtime sends. */
  joints: Record<string, number | null> | null;
  base_velocity: MeasuredBaseVelocity | null;
  /** What the HOST says about its own lease, as the tap passed it on. It is not what the runtime holds. */
  lease: TapLease | null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function tapView(
  sample: TapSample | null,
  receivedAtMs: number | null,
  nowMs: number,
  silentAfterMs: number = SILENT_AFTER_MS,
): TapView {
  const status = statusOf(sample, receivedAtMs, nowMs, silentAfterMs);
  const domains = sample?.clock_domains ?? null;
  return {
    ...status,
    source_stamp: sample?.source_stamp ?? null,
    sample_clock_domain: sample?.source_stamp?.clock_domain ?? domains?.sample ?? null,
    receive_clock_domain: domains?.receive ?? null,
    clock_domains: domains,
    receive_age_ms: finite(sample?.receive_age_ms),
    measured_rate_hz: finite(sample?.measured_rate_hz),
    subscriber_gap_count: finite(sample?.subscriber_gap_count),
    publisher_drop_count: finite(sample?.publisher_drop_count),
    session_changes: finite(sample?.session_changes),
    malformed_count: finite(sample?.malformed_count),
    transport_errors: finite(sample?.transport_errors),
    joints: sample?.joints ?? null,
    base_velocity: sample?.base_velocity ?? null,
    lease: sample?.lease ?? null,
  };
}

/**
 * THE THREE AGES, AND WHICH OF THEM MAY BE COMPUTED AT ALL.
 *
 *   CAPTURE AGE   how long ago the picture was taken. It spans the camera's monotonic clock and this laptop's, and
 *                 two monotonic clocks on two machines have no relation until somebody MEASURES one. Until then this
 *                 is null and carries the reason it could not be taken.
 *   RECEIVE AGE   how long ago the runtime received it, measured in the runtime's own clock alone. Always available
 *                 and never a substitute for the capture age: it says nothing about when the picture was taken.
 *   DECISION AGE  how old the evidence a verdict consumed was at the verdict. It belongs to the predicate record, not
 *                 to a source slot, and `model/predicate.ts` derives it inside one session and one clock domain.
 *
 * The ONE thing that licenses a capture age is `clock_relation`: a measured offset between the two named domains with
 * an uncertainty it is willing to state. This checks the relation's own ordered pair against the domain the capture
 * stamp was read in before it allows the number through, so a relation measured between two other clocks cannot be
 * used to license a subtraction it says nothing about. A runtime that reports a capture age WITHOUT a relation gets
 * the number refused with this console's own reason, because an age nobody can say how they measured is not evidence.
 */
function licensedCaptureAge(sample: PreviewSample | null): { ms: number | null; reason: string | null } {
  const reported = finite(sample?.capture_age_ms);
  const stated = sample?.capture_age_reason ?? null;
  if (reported === null) return { ms: null, reason: stated };
  const relation: ClockRelation | null = sample?.clock_relation ?? null;
  const captureDomain = sample?.capture_clock?.clock_domain ?? null;
  const licensed =
    relation !== null &&
    captureDomain !== null &&
    relation.domains[0] === captureDomain &&
    relation.domains[1] !== relation.domains[0] &&
    Number.isFinite(relation.uncertainty_ns);
  if (licensed) return { ms: reported, reason: stated };
  return { ms: null, reason: stated ?? 'capture_age_without_a_measured_clock_relation' };
}

export interface PreviewView extends SourceStatus {
  /** A camera ROLE, never a device id. Null when nothing named one. */
  role: string | null;
  /** The preview sequence this runtime counts by. */
  seq: number | null;
  /** The camera service's own sequence number, when it sent one. */
  source_seq: number | null;
  capture_clock: SourceStamp | null;
  capture_clock_domain: string | null;
  /** What the capture stamp means, in the camera service's words. Null when it did not say. */
  capture_semantics: string | null;
  /** Null whenever the two clocks have no measured relation. `capture_age_reason` says which case this is. */
  capture_age_ms: number | null;
  capture_age_reason: string | null;
  /** The measured relation that licensed a capture age, or null. It is what the screen shows the age rests on. */
  clock_relation: ClockRelation | null;
  /** Capture to dequeue, inside ONE clock. The only latency across this path that can be measured at all. */
  service_latency_ms: number | null;
  receive_age_ms: number | null;
  measured_fps: number | null;
  frame: { width_px: number | null; height_px: number | null } | null;
  source_fps_reported: number | null;
  source_fps_measured: number | null;
  pixel_format: string | null;
  gaps: number | null;
  session_changes: number | null;
  capture_time_stalls: number | null;
  malformed: number | null;
  /**
   * Previews the camera service stood aside for while a decision capture was in flight. That is the source YIELDING
   * to the controller, which is the behaviour this system is built on, and it is not a stall: a console that showed
   * it as one would send somebody to look for a broken camera.
   */
  skipped_for_mission: number | null;
  /** True only when a picture may be shown as a moving stream right now. */
  streamable: boolean;
}

export function previewView(
  sample: PreviewSample | null,
  receivedAtMs: number | null,
  nowMs: number,
  silentAfterMs: number = SILENT_AFTER_MS,
): PreviewView {
  const status = statusOf(sample, receivedAtMs, nowMs, silentAfterMs);
  const stream = sample?.stream ?? null;
  const capture = licensedCaptureAge(sample);
  return {
    ...status,
    role: sample?.role ?? null,
    seq: finite(sample?.seq),
    source_seq: finite(sample?.source_seq),
    capture_clock: sample?.capture_clock ?? null,
    capture_clock_domain: sample?.capture_clock?.clock_domain ?? null,
    capture_semantics: sample?.capture_semantics ?? null,
    capture_age_ms: capture.ms,
    capture_age_reason: capture.reason,
    clock_relation: sample?.clock_relation ?? null,
    service_latency_ms: finite(sample?.service_latency_ms),
    receive_age_ms: finite(sample?.receive_age_ms),
    measured_fps: finite(sample?.measured_fps),
    frame: sample?.frame ?? null,
    source_fps_reported: finite(stream?.source_fps_reported),
    source_fps_measured: finite(stream?.source_fps_measured),
    pixel_format: typeof stream?.pixel_format === 'string' ? stream.pixel_format : null,
    gaps: finite(sample?.gaps),
    session_changes: finite(sample?.session_changes),
    capture_time_stalls: finite(sample?.capture_time_stalls),
    malformed: finite(sample?.malformed),
    skipped_for_mission: finite(sample?.skipped_for_mission),
    // `available` alone is not enough: a runtime that says available and has stopped reaching this browser must not be
    // shown as a moving picture. Both have to hold.
    streamable: status.live && sample?.available === true,
  };
}
