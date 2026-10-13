// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHERE THE NUMBERS COME FROM: the host telemetry tap and the camera preview, each with its identity, its state and
// its freshness, as the runtime states them.
//
// The two things this panel is careful about:
//
//   THREE DIFFERENT ABSENCES. Not attached, disconnected and stalled are three different facts and they are drawn
//   differently, each with the runtime's own reason code. A stalled source keeps its LAST stamp and the age of it, and
//   shows no values at all; a source that was never configured shows nothing and says there is nothing to connect to.
//
//   TWO CLOCKS THAT ARE NEVER SUBTRACTED. The sample stamp is in the host's clock domain and the receive age is in the
//   laptop's; the panel names both. A preview's capture age is null on purpose, with the runtime's reason for why it
//   cannot be computed, and the one latency that IS inside a single clock is labelled as what it is.

import type { ReactNode } from 'react';
import type { SourceStamp } from '@kiwi/demo-contracts';
import '../styles/sources.css';
import { millis, number, textOr, UNAVAILABLE } from '../model/format';
import { availabilitySentence, previewView, SILENT_AFTER_MS, tapView } from '../model/sources';
import type { PreviewView, SourceAvailability, TapView } from '../model/sources';
import type { StoreSnapshot } from '../store/store';
import { TELEMETRY_SILENT_AFTER_MS } from '../model/steps';
import { utcDayOf, utcStamp } from '../model/utcTime';
import type { UtcStamp } from '../model/utcTime';
import { Age } from '../ui/Age';
import { Badge } from '../ui/Badge';
import type { BadgeTone } from '../ui/Badge';
import { IconCamera, IconLink } from '../ui/Icons';
import { Field, Fields, Panel } from '../ui/Panel';

const AVAILABILITY_TONE: Record<SourceAvailability, BadgeTone> = {
  connected: 'ok',
  connecting: 'info',
  stalled: 'warn',
  disconnected: 'bad',
  not_attached: 'quiet',
  failed: 'bad',
  silent: 'warn',
  never_arrived: 'quiet',
  unstated: 'quiet',
};

const AVAILABILITY_LABEL: Record<SourceAvailability, string> = {
  connected: 'connected',
  connecting: 'connecting',
  stalled: 'stalled',
  disconnected: 'disconnected',
  not_attached: 'not attached',
  failed: 'failed',
  silent: 'silent to this tab',
  never_arrived: 'no line yet',
  unstated: 'state not stated',
};

/**
 * How long this slot has been silent, in this browser's clock.
 *
 * It is clamped at zero on purpose. The reading it is measured against advances once a second, and a line that
 * arrives between two ticks is stamped AFTER the reading, which made this print a negative number of milliseconds on
 * a source that was delivering perfectly. A stamp ahead of the clock is zero here, the same rule the shared age
 * words follow, and it is never shown as a duration that ran backwards.
 */
function silentWords(silentForMs: number | null): string {
  return silentForMs === null ? UNAVAILABLE : millis(Math.max(0, silentForMs));
}

/**
 * When the source's last line reached this tab, as a fixed UTC time. Given, it replaces "silent to this tab for", an age
 * that grew with this display's clock: Diagnostics counts nothing against it (spec 5.4, 6.8).
 */
function LastLine(props: { at: UtcStamp | null | undefined; silentForMs: number | null; lines?: LinesArriving }): ReactNode {
  if (props.at === undefined) return <Field label="silent to this tab for">{silentWords(props.silentForMs)}</Field>;
  // ARRIVING, said with the time the run of lines began and never the newest line's, which moved with every sample
  // (browser r2 N4); a silent source keeps the fixed time of its last line.
  const lines = props.lines;
  if (lines !== undefined && props.at !== null && lines.current) {
    return <Field label="lines reaching this tab">{lines.since === null ? 'arriving' : `arriving since ${lines.since.text}`}</Field>;
  }
  return <Field label="last line reached this tab at">{props.at === null ? 'no line yet' : props.at.text}</Field>;
}

/** Whether a source's lines are arriving now, and since when (`model/arrivalSince.ts`). */
export interface LinesArriving {
  current: boolean;
  since: UtcStamp | null;
}

function StateBadge(props: { availability: SourceAvailability; reason: string | null }): ReactNode {
  return (
    <Badge tone={AVAILABILITY_TONE[props.availability]} strong dot title={availabilitySentence(props.availability)}>
      {AVAILABILITY_LABEL[props.availability]}
      {props.reason === null ? '' : `: ${props.reason}`}
    </Badge>
  );
}

function stateNote(status: { availability: SourceAvailability; reason: string | null; server_state: string | null }): ReactNode {
  return (
    <>
      {availabilitySentence(status.availability)}.{' '}
      {status.reason === null ? 'The server stated no reason code.' : <>The server&apos;s reason: <code>{status.reason}</code>.</>}{' '}
      {status.server_state === null ? 'It did not state a source state at all.' : <>Its own word for the state: <code>{status.server_state}</code>.</>}
    </>
  );
}

export function TapPanel(props: { view: TapView; lastLineAt?: UtcStamp | null; lines?: LinesArriving }): ReactNode {
  const tap = props.view;
  return (
    <Panel
      title="host telemetry tap"
      extra={
        <>
          <IconLink />
          <StateBadge availability={tap.availability} reason={tap.reason} />
        </>
      }
      note={stateNote(tap)}
    >
      <Fields wide>
        <Field label="source session">{textOr(tap.source_stamp?.session ?? null)}</Field>
        <Field label="source sequence">{tap.source_stamp === null ? UNAVAILABLE : String(tap.source_stamp.seq)}</Field>
        <Field label="sample stamp">
          {tap.source_stamp?.sample_mono_ns == null ? UNAVAILABLE : `${String(tap.source_stamp.sample_mono_ns)} ns`}
        </Field>
        <Field label="sample clock domain">{textOr(tap.sample_clock_domain)}</Field>
        <Field label="receive age">{millis(tap.receive_age_ms)}</Field>
        <Field label="receive clock domain">{textOr(tap.receive_clock_domain)}</Field>
        <Field label="measured rate">{number(tap.measured_rate_hz, 1, 'Hz')}</Field>
        <Field label="subscriber gaps">{tap.subscriber_gap_count === null ? UNAVAILABLE : String(tap.subscriber_gap_count)}</Field>
        <Field label="publisher drops">{tap.publisher_drop_count === null ? UNAVAILABLE : String(tap.publisher_drop_count)}</Field>
        <Field label="host session changes">{tap.session_changes === null ? UNAVAILABLE : String(tap.session_changes)}</Field>
        <Field label="malformed samples">{tap.malformed_count === null ? UNAVAILABLE : String(tap.malformed_count)}</Field>
        <Field label="transport faults">{tap.transport_errors === null ? UNAVAILABLE : String(tap.transport_errors)}</Field>
        <LastLine at={props.lastLineAt} silentForMs={tap.silent_for_ms} lines={props.lines} />
        <Field label="values">
          {tap.joints === null ? (
            <Badge tone="quiet">none are being reported</Badge>
          ) : (
            <Badge tone="ok">joints and base velocity are being reported</Badge>
          )}
        </Field>
      </Fields>
      <p className="muted" style={{ marginTop: '0.5rem', fontSize: 'var(--text-xs)' }}>
        The sample stamp and the receive age are in two different clocks, named above, and neither is ever subtracted
        from the other. The silent-to-this-tab figure is this browser&apos;s own measurement and belongs to a third.
      </p>
    </Panel>
  );
}

export function PreviewPanel(props: { view: PreviewView; lastLineAt?: UtcStamp | null; lines?: LinesArriving }): ReactNode {
  const preview = props.view;
  return (
    <Panel
      title="camera preview source"
      extra={
        <>
          <Badge tone="quiet" title="a camera role, never a device id">
            <IconCamera /> {textOr(preview.role)}
          </Badge>
          <StateBadge availability={preview.availability} reason={preview.reason} />
        </>
      }
      note={
        <>
          {stateNote(preview)} The preview is a picture for a human and is never evidence: every result on this screen
          names the stored frame it used.
        </>
      }
    >
      <Fields wide>
        <Field label="camera role">{textOr(preview.role)}</Field>
        <Field label="preview sequence">{preview.seq === null ? UNAVAILABLE : String(preview.seq)}</Field>
        <Field label="camera sequence">{preview.source_seq === null ? UNAVAILABLE : String(preview.source_seq)}</Field>
        <Field label="measured fps">{number(preview.measured_fps, 1, 'fps')}</Field>
        <Field label="source fps reported">{number(preview.source_fps_reported, 1, 'fps')}</Field>
        <Field label="source fps measured">{number(preview.source_fps_measured, 1, 'fps')}</Field>
        <Field label="capture stamp">
          {preview.capture_clock?.sample_mono_ns == null ? UNAVAILABLE : `${String(preview.capture_clock.sample_mono_ns)} ns`}
        </Field>
        <Field label="capture clock domain">{textOr(preview.capture_clock_domain)}</Field>
        <Field label="what the stamp means">{textOr(preview.capture_semantics)}</Field>
        <Field label="capture age">{preview.capture_age_ms === null ? UNAVAILABLE : millis(preview.capture_age_ms)}</Field>
        <Field label="service latency">{number(preview.service_latency_ms, 1, 'ms')}</Field>
        <Field label="receive age">{millis(preview.receive_age_ms)}</Field>
        <Field label="frame size">
          {preview.frame?.width_px == null || preview.frame.height_px == null
            ? UNAVAILABLE
            : `${String(preview.frame.width_px)} x ${String(preview.frame.height_px)} px`}
        </Field>
        <Field label="pixel format">{textOr(preview.pixel_format)}</Field>
        <Field label="gaps">{preview.gaps === null ? UNAVAILABLE : String(preview.gaps)}</Field>
        <Field label="capture time stalls">{preview.capture_time_stalls === null ? UNAVAILABLE : String(preview.capture_time_stalls)}</Field>
        <LastLine at={props.lastLineAt} silentForMs={preview.silent_for_ms} lines={props.lines} />
      </Fields>
      <p className="muted" style={{ marginTop: '0.5rem', fontSize: 'var(--text-xs)' }}>
        {preview.capture_age_ms === null ? (
          <>
            The capture age is <strong>unavailable</strong> on purpose
            {preview.capture_age_reason === null ? '' : <>, and the server says why: <code>{preview.capture_age_reason}</code></>}. The capture
            stamp and this computer&apos;s clock have no known relation, so any age across them would be a number nobody
            measured. The service latency above is the one span inside a single clock: capture to dequeue at the source.
          </>
        ) : (
          <>The capture age was reported by the server in its own clock; it is not this browser&apos;s measurement.</>
        )}
      </p>
    </Panel>
  );
}

/**
 * What a source IS, in one line: the identity it stamps its samples with, or the reason there is none.
 *
 * A source that nothing is configured to attach to has no identity to show and is not missing one. The two read
 * differently here because they are different facts, and neither of them is the word unavailable.
 */
function identityWords(stamp: SourceStamp | null, availability: SourceAvailability, absent: string): ReactNode {
  if (stamp !== null) {
    return (
      <>
        session <span className="mono">{stamp.session}</span>, sample <span className="mono">{String(stamp.seq)}</span>
      </>
    );
  }
  if (availability === 'not_attached') return <span className="muted">nothing is configured to attach to</span>;
  return <span className="muted">{absent}</span>;
}

/**
 * THE COMPACT SOURCE SUMMARY, for the presentation screen.
 *
 * It answers the two questions somebody watching a run has about a source: is it delivering, and when did the last
 * line reach this computer. The thirty-two fields that answer the rest of them live in the engineering tab, where
 * somebody who is debugging goes looking for them, because printed here they filled a quarter of the screen with the
 * word unavailable on a machine where the camera was never configured in the first place.
 *
 * The age advances on the shared tick rather than being read off the clock while this renders, which is what used to
 * leave it standing still between the five-second polls.
 */
export function SourcesPanels(props: { snapshot: StoreSnapshot; nowMs?: number }): ReactNode {
  const now = props.nowMs ?? Date.now();
  const telemetry = props.snapshot.telemetry;
  const tap = tapView(telemetry.tap, telemetry.tap_received_at_ms, now);
  const preview = previewView(telemetry.preview, telemetry.preview_received_at_ms, now);
  return (
    <Panel
      title="sources"
      extra={<Badge tone="quiet">2 attached to this tab</Badge>}
      note="Each source is shown as the runtime states it, with its own reason code. The full identity of each one, both clock domains, the measured rates and the drop counters are in the engineering tab."
    >
      <div className="source-row">
        <span className="source-row__name">
          <IconLink /> host telemetry tap
        </span>
        <StateBadge availability={tap.availability} reason={tap.reason} />
        <span className="source-row__detail">
          {identityWords(tap.source_stamp, tap.availability, 'no sample has arrived')}. Last line{' '}
          <Age atMs={telemetry.tap_received_at_ms} nowMs={props.nowMs} staleAfterMs={SILENT_AFTER_MS} absent="no line yet" />.
        </span>
      </div>
      <div className="source-row">
        <span className="source-row__name">
          <IconCamera /> camera preview {preview.role === null ? '' : `(${preview.role})`}
        </span>
        <StateBadge availability={preview.availability} reason={preview.reason} />
        <span className="source-row__detail">
          {identityWords(preview.capture_clock, preview.availability, 'no frame has arrived')}. Last line{' '}
          <Age atMs={telemetry.preview_received_at_ms} nowMs={props.nowMs} staleAfterMs={SILENT_AFTER_MS} absent="no line yet" />.
        </span>
      </div>
    </Panel>
  );
}

/** Both sources in full, for the engineering tab: one panel each, with every field the runtime states. */
export function SourcesDetailPanels(props: { snapshot: StoreSnapshot; nowMs?: number }): ReactNode {
  const now = props.nowMs ?? Date.now();
  const telemetry = props.snapshot.telemetry;
  const day = utcDayOf(props.snapshot.mission?.started_wall_utc ?? null) ?? new Date(now).toISOString().slice(0, 10);
  const at = (ms: number | null): UtcStamp | null => (ms === null ? null : utcStamp(new Date(ms).toISOString(), day));
  const arriving = (lastMs: number | null, sinceMs: number | null): LinesArriving => ({ current: lastMs !== null && now - lastMs <= TELEMETRY_SILENT_AFTER_MS, since: at(sinceMs) });
  return (
    <>
      <TapPanel view={tapView(telemetry.tap, telemetry.tap_received_at_ms, now)} lastLineAt={at(telemetry.tap_received_at_ms)} lines={arriving(telemetry.tap_received_at_ms, telemetry.tap_since_ms ?? null)} />
      <PreviewPanel view={previewView(telemetry.preview, telemetry.preview_received_at_ms, now)} lastLineAt={at(telemetry.preview_received_at_ms)} lines={arriving(telemetry.preview_received_at_ms, telemetry.preview_since_ms ?? null)} />
    </>
  );
}
