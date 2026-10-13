// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT A LOOK FOUND ABOUT THE PAD, in one sentence, from the layer that decides.
//
// The controller writes one perception.result per layer of a frame (`_perception_layers`): the cube recognizer, the
// pad OUTLINE fit, the pad PRINT landmarks and the cube in the pad frame. Decisions use the print-landmark pad frame;
// the outline fit is presentation only. On 2026-09-26 the outline fit failed on a frame whose print resolved 97
// landmarks, and the screen read as if the pad had not been found. This states the print first, with its count, and
// the outline only as the diagnostic it is.
//
// It reads the fold's bounded tail of raw records, because the per-frame map keeps one layer per frame (the last one
// written) and the pad layers are two of four.

import type { NamedValue } from '@kiwi/demo-contracts';
import { recordedNumber } from './stageTimeline';
import type { ViewState } from './types';

export interface ObserveWords {
  observation_id: string;
  /** The print-landmark result: the pad frame, with the count and the reprojection, or why there is none. */
  pad: string;
  /** The outline fit, only when it did not converge; null when it did or was not recorded. */
  outline: string | null;
}

function valueOf(measurements: readonly NamedValue[], name: string): NamedValue['value'] | null {
  return measurements.find((item) => item.name === name)?.value ?? null;
}

export function observeSentence(view: ViewState): ObserveWords | null {
  let landmarks: { id: string; result: string; measurements: NamedValue[] } | null = null;
  for (let index = view.recent_events.length - 1; index >= 0; index -= 1) {
    const event = view.recent_events[index];
    if (event === undefined || event.kind !== 'perception.result' || event.payload.detector !== 'pad-print-landmarks') continue;
    const id = event.payload.observation_ids[0];
    if (id === undefined) continue;
    landmarks = { id, result: event.payload.result, measurements: event.payload.measurements };
    break;
  }
  if (landmarks === null) return null;
  const id = landmarks.id;

  let outlineConverged: boolean | null = null;
  for (const event of view.recent_events) {
    if (event.kind !== 'perception.result' || event.payload.detector !== 'pad-outline-fit') continue;
    if (!event.payload.observation_ids.includes(id)) continue;
    outlineConverged = event.payload.result === 'detected';
  }

  const inliers = valueOf(landmarks.measurements, 'landmark_inliers');
  const rms = valueOf(landmarks.measurements, 'reprojection_rms');
  const count = typeof inliers === 'number' ? `${String(inliers)} landmarks` : 'landmark count not recorded';
  const reprojection = typeof rms === 'number' ? `, reprojection ${recordedNumber(rms, 'px') ?? ''}` : '';
  const valid = landmarks.result === 'detected' && valueOf(landmarks.measurements, 'pad_frame_valid') !== false;
  const template = valueOf(landmarks.measurements, 'template_available');

  const pad =
    landmarks.result === 'error' || template === false
      ? 'No pad print template was available for this frame, so no pad frame was measured.'
      : valid
        ? `Pad located by its print (${count}${reprojection}).`
        : `The pad print was not resolved in this frame (${typeof inliers === 'number' ? `${String(inliers)} landmarks matched` : 'landmark count not recorded'}); no position on the pad is measured from it.`;
  return {
    observation_id: id,
    pad,
    outline: outlineConverged === false ? 'The outline fit did not converge on this frame; it is a diagnostic and decides nothing.' : null,
  };
}
