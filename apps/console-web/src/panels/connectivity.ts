// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT IS CONNECTED TO WHAT. Three questions, never one badge.
//
// The strip used to carry a single group called "link and freshness", driven by the snapshot's `link`. That field is
// one fact: whether the RUNTIME is holding a link to a robot host. On a simulation with no robot it reads `unknown`,
// which the strip printed as the word unknown while the API was answering every poll and lines were arriving several
// times a second. Everything on the screen contradicted the one badge that claimed to describe it.
//
// They are separate questions and each is answered from the thing that knows it:
//
//   CONSOLE           is the API answering THIS browser, and are its stream lines reaching this page? Measured here,
//                     on this computer's clock, from the arrival stamps the store takes when a line lands. The age
//                     beside it is TRANSPORT ACTIVITY and nothing else: it says when a line last arrived on the
//                     stream, and it says nothing whatever about the robot or about when anything was measured. It
//                     stood beside the words "lines are arriving" reading "7 min 59 s ago", which looks like a
//                     contradiction and is two different measurements printed as one. Each fact now carries its own
//                     label: `model/measurement.ts` holds the three and what each of them measures.
//   ROBOT TELEMETRY   is the runtime holding the telemetry tap open, and are its samples reaching this page? The
//                     runtime's own `link` for the first half, the tap's arrival stamp for the second.
//   EACH CAMERA       one readout per camera the runtime names, never one badge for all of them. A camera that has a
//                     live preview slot reports the stream; one that has not reports what the runtime recorded about
//                     its captures, with the runtime's own evidence.
//
// AN ABSENT CAMERA IN A SIMULATION IS NOT BROKEN HARDWARE. When the runtime says it is in synthetic mode, a missing
// robot host and a missing camera are the state that mode is expected to be in, and the sentence says so instead of
// leaving the operator to read an absence as a fault.

import type { Component } from '@kiwi/demo-contracts';
import { availabilitySentence, previewView, tapView } from '../model/sources';
import type { PreviewView, TapView } from '../model/sources';
import { AGE_MEANING, TRANSPORT_ACTIVITY_LABEL } from '../model/measurement';
import type { StoreSnapshot } from '../store/store';
import type { BadgeTone } from '../ui/Badge';

export type ConnectivityKind = 'console' | 'robot' | 'camera';

/** One independent readout: its own words, its own tone, and its own arrival stamp where it has one. */
export interface Readout {
  id: string;
  /** What the thing is, in the operator's words. */
  label: string;
  kind: ConnectivityKind;
  tone: BadgeTone;
  /** The state, in words. Status is never carried by colour alone, so this is never empty. */
  words: string;
  /** The whole sentence, including the runtime's own reason, shown on hover. */
  detail: string;
  /** When the last line of this thing reached THIS browser, or null when none has. */
  atMs: number | null;
  /**
   * WHAT THAT AGE MEASURES, in the one word set aside for it. Every age on this screen belongs to exactly one of three
   * facts and no two of them may share a word: see `model/measurement.ts`. A readout that states none is stating a
   * transport age, which is the only kind a connectivity readout has.
   */
  age_label?: string;
  /** What to say in place of an age when nothing has arrived. It is never the word unavailable. */
  absent: string;
}

/**
 * How the runtime names a camera in its component list: the role, then this suffix. It is the runtime's convention
 * (`read_models.components`), stated here once so a readout can pair a camera device with the preview slot of the
 * same role. A device whose id does not follow it keeps its id as its role and is still reported.
 */
const CAMERA_SUFFIX = '-camera';

/** The sentence a simulation adds, so that an expected absence is not read as a fault. */
const EXPECTED_IN_SIMULATION =
  ' The runtime reports itself in synthetic mode, where there is no robot and no camera, so this is the state that mode is expected to be in.';

function newer(first: number | null, second: number | null): number | null {
  if (first === null) return second;
  if (second === null) return first;
  return Math.max(first, second);
}

/** Is the API answering this browser, and are its lines arriving here? */
export function consoleReadout(snapshot: StoreSnapshot): Readout {
  const view = snapshot.view;
  const lastLine = newer(snapshot.telemetry.tap_received_at_ms, snapshot.telemetry.preview_received_at_ms);
  const base = { id: 'console', label: 'console', kind: 'console' as const, absent: 'no stream line has arrived' };

  if (!view.contract_compatible) {
    return {
      ...base,
      tone: 'bad',
      words: 'refused',
      detail: `this runtime speaks a contract version this console does not (${view.read_only_reason ?? 'unknown'}), so nothing below is being updated`,
      atMs: null,
    };
  }
  if (snapshot.sourceError !== null) {
    return {
      ...base,
      tone: 'bad',
      words: 'the last exchange failed',
      detail: `${snapshot.sourceError}. What is on the screen is the last state that did arrive.`,
      atMs: lastLine,
    };
  }
  if (lastLine !== null) {
    return {
      ...base,
      tone: 'ok',
      words: 'lines are arriving',
      detail: `the API answered the last read and its stream is reaching this browser. The age beside this is ${TRANSPORT_ACTIVITY_LABEL}: ${
        AGE_MEANING[TRANSPORT_ACTIVITY_LABEL] ?? ''
      } It is measured on this computer's own clock.`,
      atMs: lastLine,
      age_label: TRANSPORT_ACTIVITY_LABEL,
    };
  }
  if (view.runtime_session !== null) {
    return {
      ...base,
      tone: 'ok',
      words: 'reads are answered',
      detail: 'the API answered the last read of the runtime, and no stream line has reached this browser yet',
      atMs: null,
    };
  }
  return { ...base, tone: 'quiet', words: 'nothing has been read yet', detail: 'no read of the runtime has come back in this tab', atMs: null };
}

/** Is the runtime holding the tap to the robot host, and are its samples arriving here? */
export function robotReadout(snapshot: StoreSnapshot, nowMs: number): Readout {
  const view = snapshot.view;
  const tap: TapView = tapView(snapshot.telemetry.tap, snapshot.telemetry.tap_received_at_ms, nowMs);
  const simulation = view.data_mode === 'synthetic';
  const base = {
    id: 'robot',
    label: 'robot telemetry',
    kind: 'robot' as const,
    atMs: snapshot.telemetry.tap_received_at_ms,
    absent: 'no sample has arrived',
  };
  const seq = view.link.source_stamp?.seq ?? null;
  const stamped = seq === null ? '' : ` The last host observation the runtime received carries source seq ${String(seq)}.`;

  if (tap.live) {
    return { ...base, tone: 'ok', words: 'samples arriving', detail: `${availabilitySentence(tap.availability)}.${stamped}` };
  }
  if (view.link.state === 'connected') {
    return {
      ...base,
      tone: 'warn',
      words: tap.availability === 'never_arrived' ? 'link held, no sample here yet' : 'link held, samples stopped here',
      detail: `the runtime says it holds the link, and ${availabilitySentence(tap.availability)}.${stamped}`,
    };
  }
  if (view.link.state === 'disconnected') {
    return {
      ...base,
      tone: 'bad',
      words: 'no link to a robot host',
      detail: `the runtime is not holding a link to a robot host.${stamped}${simulation ? EXPECTED_IN_SIMULATION : ''}`,
    };
  }
  return {
    ...base,
    tone: 'quiet',
    words: 'no host observation yet',
    detail: `nothing has been received from a robot host in this runtime session.${simulation ? EXPECTED_IN_SIMULATION : ''}`,
  };
}

function cameraWords(health: Component['health']): { tone: BadgeTone; words: string } {
  if (health === 'ok') return { tone: 'ok', words: 'captures recorded' };
  if (health === 'degraded') return { tone: 'warn', words: 'the runtime reports it degraded' };
  if (health === 'failed') return { tone: 'bad', words: 'the runtime reports it failed' };
  return { tone: 'quiet', words: 'nothing captured yet' };
}

function previewWords(preview: PreviewView): { tone: BadgeTone; words: string } {
  if (preview.live) return { tone: 'ok', words: 'preview arriving' };
  if (preview.availability === 'not_attached') return { tone: 'quiet', words: 'no preview is configured' };
  if (preview.availability === 'never_arrived') return { tone: 'quiet', words: 'no preview line has arrived' };
  if (preview.availability === 'stalled' || preview.availability === 'silent') return { tone: 'warn', words: `preview ${preview.availability}` };
  return { tone: 'warn', words: `preview ${preview.availability}` };
}

/**
 * One readout per camera the runtime names, plus one for a preview slot whose role no named camera matches.
 *
 * A camera with a live preview slot reports the STREAM, because that is the stronger and more current fact; the
 * capture evidence stays in the sentence beside it. A camera with no preview line reports what the runtime recorded
 * about its captures, in the runtime's own evidence.
 */
export function cameraReadouts(snapshot: StoreSnapshot, nowMs: number): Readout[] {
  const view = snapshot.view;
  const simulation = view.data_mode === 'synthetic';
  const preview = previewView(snapshot.telemetry.preview, snapshot.telemetry.preview_received_at_ms, nowMs);
  const previewRole = snapshot.telemetry.preview === null ? null : preview.role;
  const rows: Readout[] = [];
  let previewPaired = false;

  for (const device of view.components.filter((component) => component.layer === 'device')) {
    const role = device.id.endsWith(CAMERA_SUFFIX) ? device.id.slice(0, -CAMERA_SUFFIX.length) : device.id;
    const streaming = previewRole !== null && previewRole === role;
    if (streaming) previewPaired = true;
    const chosen = streaming ? previewWords(preview) : cameraWords(device.health);
    const expected = !streaming && device.health === 'unknown' && simulation ? EXPECTED_IN_SIMULATION : '';
    rows.push({
      id: `camera:${device.id}`,
      label: role,
      kind: 'camera',
      tone: chosen.tone,
      words: chosen.words,
      detail: streaming
        ? `${availabilitySentence(preview.availability)}. What the runtime recorded about its captures: ${device.evidence}.`
        : `${device.evidence}.${expected}`,
      atMs: streaming ? snapshot.telemetry.preview_received_at_ms : null,
      absent: streaming ? 'no preview line has arrived' : 'no preview slot for this camera',
    });
  }

  if (previewRole !== null && !previewPaired) {
    const chosen = previewWords(preview);
    rows.push({
      id: `camera:preview:${previewRole}`,
      label: previewRole,
      kind: 'camera',
      tone: chosen.tone,
      words: chosen.words,
      detail: `${availabilitySentence(preview.availability)}. This runtime names no camera component for this role.`,
      atMs: snapshot.telemetry.preview_received_at_ms,
      absent: 'no preview line has arrived',
    });
  }

  if (rows.length === 0) {
    rows.push({
      id: 'camera:none',
      label: 'cameras',
      kind: 'camera',
      tone: 'quiet',
      words: 'none is reported',
      detail: `this runtime has not named a camera.${simulation ? EXPECTED_IN_SIMULATION : ''}`,
      atMs: null,
      absent: 'no camera has reported',
    });
  }
  return rows;
}

/** The three questions in the order the strip asks them: this browser, the robot, then each camera. */
export function connectivityReadouts(snapshot: StoreSnapshot, nowMs: number): Readout[] {
  return [consoleReadout(snapshot), robotReadout(snapshot, nowMs), ...cameraReadouts(snapshot, nowMs)];
}

/**
 * The artifact that is actually executing, taken from a record that NAMED it.
 *
 * Two things went wrong in the strip before this. The name and the digest were two separate fallbacks, so a tab with
 * no record folded yet printed the word unavailable twice in a row, which reads as a runtime that answered twice and
 * failed twice. And it read the producer off the LAST record only, so a tab opened after a run had finished, or one
 * whose bounded tail had rolled, went back to saying nothing although a record had named the producer moments
 * earlier. The whole fold is searched, newest first, and the session's producer list is the second source; only when
 * neither carries one does this say so, in one sentence.
 */
export interface BackendClaim {
  name: string | null;
  version: string | null;
  artifact_digest: string | null;
  /** The record this rests on, so the claim can be checked. Null when nothing named a producer. */
  basis: string | null;
}

export function executionBackend(snapshot: StoreSnapshot): BackendClaim {
  const events = snapshot.view.recent_events;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const producer = event?.producer ?? null;
    if (producer !== null && typeof producer.name === 'string' && producer.name !== '') {
      return {
        name: producer.name,
        version: producer.version,
        artifact_digest: producer.artifact_digest,
        basis: `named by the record at seq ${String(event?.seq ?? 0)}`,
      };
    }
  }
  const sessions = snapshot.view.source_sessions;
  let newest: (typeof sessions)[number] | null = null;
  for (const session of sessions) {
    if (newest === null || session.last_seq > newest.last_seq) newest = session;
  }
  if (newest !== null) {
    return {
      name: newest.producer,
      version: null,
      artifact_digest: null,
      basis: `named by the records of session ${newest.session}, up to seq ${String(newest.last_seq)}`,
    };
  }
  return { name: null, version: null, artifact_digest: null, basis: null };
}
