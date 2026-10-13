// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THE CAMERA CARD IS IN, decided from the source's own state and from what this screen is.
//
// This exists because one line in the panel decided it before: a preview that could not be streamed was called
// DEGRADED, and nothing may be streamed outside live mode, so every simulation said DEGRADED CAMERA at an unconfigured
// runtime that has no camera and is not supposed to have one. Degraded is a claim about hardware. An absent camera in
// a simulation that never configured one is a fixture state, and an operator sent to look for a broken camera by a
// screen that had nothing to report is worse served than one told plainly that there is nothing to connect to.
//
// Three further facts are kept apart here rather than folded into one word:
//
//   NOT CONFIGURED is not DISCONNECTED. Nothing to connect to, and a connection that is not being held, are different
//   things to do something about, and the runtime already states which it has.
//   YIELDING IS NOT FAILING. The camera service stands aside while the controller takes a decision capture. That is
//   the behaviour this system is built on: the mission's capture is the evidence, the preview is only for watching.
//   SILENT TO THIS TAB is measured in the BROWSER's clock, on arrivals, and is never mixed with the runtime's ages.

import { availabilitySentence } from './sources';
import type { PreviewView } from './sources';
import type { DataMode } from '@kiwi/demo-contracts';
import type { CardState } from '../ui/CardState';

export interface CameraReading {
  /** Which of the shared card states the camera card is in. */
  state: CardState;
  /** The exact phrase for this card, which is never the bare word unavailable for a camera nobody configured. */
  words: string;
  /** What is true, in a sentence, with the runtime's own reason where the runtime gave one. */
  sentence: string;
  /** True when a moving picture may be shown right now. */
  stream: boolean;
  /**
   * True when the absence is the expected state of this fixture rather than something to go and look at. An
   * unconfigured simulation and a replay are expected; a stalled camera on a live runtime is not.
   */
  expected: boolean;
  /** The camera role the runtime published, or null when it published none. Never a guess at one. */
  role: string | null;
  /** How many previews the camera service stood aside for while the controller captured. Null when unreported. */
  yielded_for_mission: number | null;
}

/**
 * The camera card's state.
 *
 * `silentForMs` is how long ANY telemetry line has been absent from this browser, which is what says the runtime's own
 * report about the camera is too old to present as current. It is separate from the source's own state on purpose: a
 * runtime that says connected and has stopped reaching this tab is not a connected camera to the reader of this page.
 */
export function cameraReading(input: {
  camera: PreviewView;
  /** The runtime's data mode, or null while no snapshot has stated it. */
  mode: DataMode | null;
  sourceKind: 'live' | 'synthetic';
  silentForMs: number | null;
  silentAfterMs: number;
}): CameraReading {
  const { camera, mode, sourceKind } = input;
  const silent = input.silentForMs !== null && input.silentForMs > input.silentAfterMs;
  const reason = camera.reason === null ? '' : ` The runtime's reason: ${camera.reason}.`;
  const base = {
    role: camera.role,
    yielded_for_mission: camera.skipped_for_mission,
  };

  // The script inside this page carries no camera bytes at all, and there is no runtime behind it to have a camera.
  if (sourceKind === 'synthetic' && mode === 'synthetic') {
    return {
      ...base,
      state: 'idle',
      words: 'No camera in this simulation',
      sentence: 'The script inside this page carries no camera bytes. Nothing is connected to a camera here and nothing is expected to be.',
      stream: false,
      expected: true,
    };
  }

  if (mode === 'replay') {
    return {
      ...base,
      state: 'idle',
      words: 'Recorded run',
      sentence: 'This screen is replaying a recording. A recording carries the frames the controller stored, not a camera, so no stream is shown.',
      stream: false,
      expected: true,
    };
  }

  // Nothing was configured to attach to. True of a runtime in simulation, which is the ordinary case, and true of a
  // live runtime launched without a camera service; in both the runtime has said there is nothing to connect to.
  if (camera.availability === 'not_attached') {
    return {
      ...base,
      state: 'idle',
      words: 'No camera configured',
      sentence: `No camera source is configured on this runtime: there is nothing to connect to.${reason}`,
      stream: false,
      expected: true,
    };
  }

  if (camera.availability === 'never_arrived') {
    return {
      ...base,
      state: 'waiting',
      words: 'No preview line yet',
      sentence: 'No preview line has reached this browser at all. The runtime publishes the preview slot only when something publishes a preview.',
      stream: false,
      expected: mode !== 'live',
    };
  }

  // A camera that IS publishing, next to records that were not produced by one. The rule the console keeps is that a
  // moving picture is never shown beside records from another mode, because a picture next to a record reads as a
  // picture OF that record.
  // A MODE NOBODY HAS STATED is not a simulation: the picture is held back for the same reason, and said as that reason.
  if (mode === null) {
    return {
      ...base,
      state: 'idle',
      words: 'Stream held back',
      sentence:
        'This runtime is publishing a camera preview and no snapshot has stated its mode yet. A moving picture is not shown until the records beside it are known to be live.',
      stream: false,
      expected: true,
    };
  }
  if (mode !== 'live') {
    return {
      ...base,
      state: 'idle',
      words: 'Stream held back',
      sentence:
        'This runtime is publishing a camera preview and the records on this screen are simulated. A moving picture is not shown beside simulated records: it would read as a picture of them.',
      stream: false,
      expected: true,
    };
  }

  if (camera.availability === 'failed') {
    return { ...base, state: 'failed', words: 'Transport failed', sentence: `The transport itself broke.${reason}`, stream: false, expected: false };
  }

  if (camera.availability === 'disconnected') {
    return {
      ...base,
      state: 'blocked',
      words: 'Not connected',
      sentence: `The runtime is not holding the camera source open.${reason}`,
      stream: false,
      expected: false,
    };
  }

  if (camera.availability === 'connecting') {
    return {
      ...base,
      state: 'waiting',
      words: 'Connecting',
      sentence: `The runtime has the socket open and no frame has come through it yet.${reason}`,
      stream: false,
      expected: false,
    };
  }

  if (camera.availability === 'stalled') {
    return {
      ...base,
      state: 'stale',
      words: 'Stalled at the runtime',
      sentence: `Frames arrived and then stopped. The last stamp is kept and its values are not shown as current.${reason}`,
      stream: false,
      expected: false,
    };
  }

  if (camera.availability === 'unstated') {
    return {
      ...base,
      state: 'unknown',
      words: 'State not stated',
      sentence: `What arrived did not say what state the camera source is in.${reason}`,
      stream: false,
      expected: false,
    };
  }

  // Connected at the runtime, and the line to this tab has stopped. What the runtime last said is at least that old.
  if (silent || camera.availability === 'silent') {
    return {
      ...base,
      state: 'stale',
      words: 'Silent to this tab',
      sentence: `${availabilitySentence('silent')}, so what the runtime last said about the camera is at least that old and is not shown as current.`,
      stream: false,
      expected: false,
    };
  }

  if (camera.streamable) {
    return {
      ...base,
      state: 'active',
      words: 'Streaming',
      sentence: 'Frames are arriving and the runtime holds the camera open. The stream is for watching: every result on this screen names the stored frame it used.',
      stream: true,
      expected: false,
    };
  }

  // Connected, and the runtime says the preview is not available to be shown. Its own reason is the whole answer.
  return {
    ...base,
    state: 'blocked',
    words: 'Not available to show',
    sentence: `The runtime holds the camera source and says no preview is available to show.${reason}`,
    stream: false,
    expected: false,
  };
}

/**
 * The query parameter names this console refuses to put a picture behind. A credential in a URL reaches the browser's
 * history, the referrer of anything the page loads next and every access log on the way; the API's guard takes its
 * credential in a HEADER, and the boot element says so, so a media URL that carries one is a URL built the wrong way.
 */
const CREDENTIAL_SHAPED_PARAMS: readonly string[] = [
  'access_token',
  'api_key',
  'auth',
  'authorization',
  'credential',
  'credentials',
  'key',
  'password',
  'secret',
  'sig',
  'signature',
  'token',
];

/**
 * A media URL this page may point an `img` at, or a refusal with the reason.
 *
 * Every picture on this screen is an immutable stored frame or the preview stream, and both are served through the
 * console API on this page's own origin: `/api/observations/{id}/frame` and `/api/preview.mjpg`. A URL that names a
 * host is a path out of the proxy, past the API's guard and past the boot element's credential; a URL that carries a
 * credential-shaped parameter has put a secret somewhere it cannot be taken back from. Neither is loaded, and the
 * panel says which one it refused rather than showing a broken picture.
 */
export function proxiedMediaUrl(url: string | null): { url: string | null; refusal: string | null } {
  if (url === null) return { url: null, refusal: null };
  if (!url.startsWith('/') || url.startsWith('//')) {
    return { url: null, refusal: 'this picture would be loaded from outside the console API on this page, and is not shown' };
  }
  const query = url.includes('?') ? url.slice(url.indexOf('?') + 1) : '';
  for (const piece of query.split('&')) {
    const name = decodeURIComponent(piece.split('=')[0] ?? '').toLowerCase();
    if (CREDENTIAL_SHAPED_PARAMS.includes(name)) {
      return { url: null, refusal: `the address of this picture carries a credential-shaped parameter (${name}), and is not shown` };
    }
  }
  return { url, refusal: null };
}

/**
 * The words for a capture age that cannot be taken, and the one measurement that can.
 *
 * The capture stamp is read on the camera's machine and the receive stamp on the runtime's, and two monotonic clocks
 * on two machines have no relation until somebody measures one. The contract now has a place to state that relation,
 * with the uncertainty it is willing to state, and nothing fills it in today; until something does, the honest answer
 * is that the capture age is not known, and the receive age is shown beside it as the different quantity it is.
 */
export function captureAgeSentence(camera: PreviewView): string {
  if (camera.capture_age_ms !== null) {
    const relation = camera.clock_relation;
    const uncertainty = relation === null ? 'an unstated uncertainty' : `an uncertainty of ${(relation.uncertainty_ns / 1e6).toFixed(1)} ms`;
    return `The capture age rests on a measured relation between ${camera.capture_clock_domain ?? 'the capture clock'} and the runtime's clock, with ${uncertainty}.`;
  }
  const stated = camera.capture_age_reason === null ? '' : ` The runtime's reason: ${camera.capture_age_reason}.`;
  return `How long ago the picture was taken is not known: the camera's clock and this runtime's have no measured relation, and no console may subtract two monotonic clocks on two machines.${stated}`;
}
