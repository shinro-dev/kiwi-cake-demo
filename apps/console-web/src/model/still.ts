// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHICH STORED FRAME THE CAMERA CARD DRAWS, AND WHAT IT SAYS WHEN IT DRAWS NONE.
//
// This file exists because of one sentence a running console put on the screen while a real frame was on disk. A
// non-motion Observe ran on hardware, the controller captured a wrist frame at 1280x720 and retained it, and the
// console API answered the exact URL the page builds with 200 image/jpeg and 52412 bytes. A tab opened after that run
// said: "Waiting for image. The controller has stored no frame yet." That sentence was false, and it was false for a
// structural reason rather than a rendering one: the browser built its whole list of observations by folding
// observation.captured records off the event stream, and a tab that connects or reloads AFTER a run folds none of
// them. Nothing else in its state named a frame, so the card had nothing to draw and reported that nothing existed.
//
// TWO SOURCES, DECIDED BY THE ORDERING THAT ALREADY EXISTS. The rule here used to be that the fold always wins over
// the snapshot, on the reasoning that a record folded off the stream is newer than a snapshot built up to one poll
// ago. That is a guess about arrival, and arrival is the thing that races: a record folded minutes ago is OLDER than
// an arriving snapshot naming a capture taken since, and a snapshot delayed in flight is older than what has been
// folded while it travelled. Preferring either source is wrong in one of those two directions.
//
// So the decision is made on the SEQUENCE NUMBER both sources carry, in both directions, and the card reports which
// source the frame it is drawing came from. `model/merge.ts` holds that rule and the reasoning behind it, including
// why a snapshot from a different `runtime_session` is neither newer nor older but another log altogether, which the
// reducer resets on rather than merges. The decision is made here and nowhere else, so no panel can decide it
// differently.
//
// THREE STATES THAT ARE ACTUALLY DIFFERENT, because the operator asked for three and the screen had one:
//
//   NO CAPTURE YET means nothing has been captured in this runtime session. It is a statement about the session, not
//   about this tab's fold, and it may only be made when BOTH sources are empty.
//   CAPTURE FAILED means a capture was attempted and did not produce a frame anybody can look at. Two different things
//   land here and the card says which: a capture the runtime recorded without retaining a frame from it (and the
//   frame_missing evidence gap that states the same thing in the runtime's own words), and a frame that exists whose
//   IMAGE this browser asked for and did not get. Neither is ever called "no capture yet".
//   CONTINUOUS PREVIEW UNAVAILABLE is about VIDEO and is computed separately, in `previewReading` at the bottom. No
//   live video source is attached on this baseline, which is the ordinary state and not a fault; a still being present
//   while video is absent is the ordinary case and reads as one. The two are never folded into one sentence, because
//   folding them is how "no video" came to be shown to an operator as "no picture".

import { decideBySeq } from './merge';
import type { EvidenceGapView, ObservationMeta } from './types';
import type { CameraReading } from './camera';

/** A capture this card can point an `img` at: kept in the evidence store, and a single frame rather than a scalar set. */
function drawable(meta: ObservationMeta): boolean {
  return meta.retained && meta.media === 'frame';
}

export type StillState = 'shown' | 'none_yet' | 'failed';

export interface StillReading {
  /** Which of the three the card is in. Never a fourth, and never two at once. */
  state: StillState;
  /** The short words for the card, in the operator's terms. */
  words: string;
  /** The whole sentence, which never says nothing was captured while something was. */
  sentence: string;
  /** The capture to draw, or null when there is nothing to draw. */
  still: ObservationMeta | null;
  /**
   * Where that capture came from. The fold is this tab's own record of the stream; the snapshot is the runtime's
   * answer, which is what a tab that missed the capture has. The card prints it, because an operator comparing two
   * tabs is entitled to know which of them folded the record and which was told about it.
   */
  from: 'folded' | 'snapshot' | null;
  /**
   * The seq of the still before this one, for the cadence line, or null when this fold has only one. It can only ever
   * come from the fold: a snapshot names one capture and knows nothing about a cadence.
   */
  previous_seq: number | null;
}

/**
 * Which stored frame to draw, and the honest sentence for the case where there is none.
 *
 * `image_failed_for` is the observation id whose IMAGE this browser asked for and did not get, as reported by the
 * element that tried to load it. It is carried in rather than guessed at: a frame that is named by a record and cannot
 * be served is a different failure from a capture that produced nothing, and an empty box with no sentence under it is
 * neither.
 */
export function stillReading(input: {
  observations: Record<string, ObservationMeta>;
  snapshot_observation: ObservationMeta | null;
  evidence_gaps: EvidenceGapView[];
  image_failed_for: string | null;
}): StillReading {
  const folded = Object.values(input.observations)
    .filter(drawable)
    .sort((left, right) => right.seq - left.seq);
  const fromSnapshot = input.snapshot_observation !== null && drawable(input.snapshot_observation) ? input.snapshot_observation : null;

  // The LATER RECORD of the one ordered log, whichever source named it. Not the fold by preference: a capture folded
  // at record 5 four minutes ago is older than a snapshot naming record 300, and a snapshot that left the runtime at
  // record 300 is older than record 340 folded while it was in flight.
  const decided = decideBySeq(folded[0] ?? null, fromSnapshot);
  const still = decided.value;
  const from = decided.from;
  // The cadence can only ever come from the fold: a snapshot names one capture and knows nothing about a cadence.
  const previous_seq = (still !== null && folded[0] !== undefined && folded[0].seq === still.seq ? folded[1]?.seq : folded[0]?.seq) ?? null;

  if (still !== null && input.image_failed_for === still.observation_id) {
    return {
      state: 'failed',
      words: 'Frame not served',
      sentence: `The controller stored frame ${still.observation_id} and this browser asked the console API for its image and did not get one. The capture is not missing: its picture could not be served to this tab.`,
      still,
      from,
      previous_seq,
    };
  }

  if (still !== null) {
    return {
      state: 'shown',
      words: 'Stored frame',
      sentence:
        from === 'folded'
          ? 'The picture below is the newest frame the controller stored, as this tab folded it off the record stream. It is a still, not video.'
          : 'The picture below is the newest frame the controller stored, as the runtime named it on the snapshot. It is later in the record than anything this tab folded, which is the ordinary case for a tab opened or reloaded after the capture. It is a still, not video.',
      still,
      from,
      previous_seq,
    };
  }

  // Nothing can be drawn. Before saying nothing was captured, look for a capture that was attempted and produced
  // nothing, because those two are the sentences the operator asked never to be confused again.
  const gap = input.evidence_gaps.filter((item) => item.gap === 'frame_missing').sort((left, right) => right.seq - left.seq)[0] ?? null;
  if (gap !== null) {
    return {
      state: 'failed',
      words: 'Capture failed',
      sentence: `A capture was attempted and no frame came out of it: the runtime recorded a frame_missing gap at record ${String(gap.seq)}. ${gap.detail}`,
      still: null,
      from: null,
      previous_seq: null,
    };
  }

  const attempted = Object.values(input.observations).sort((left, right) => right.seq - left.seq)[0] ?? null;
  if (attempted !== null) {
    return {
      state: 'failed',
      words: 'Capture failed',
      sentence: attempted.retained
        ? `A capture was recorded at record ${String(attempted.seq)} and what it produced is a ${attempted.media}, which is not a picture this card can draw.`
        : `A capture was recorded at record ${String(attempted.seq)} and no frame was retained from it, so there is nothing stored to serve.`,
      still: null,
      from: null,
      previous_seq: null,
    };
  }

  return {
    state: 'none_yet',
    words: 'No capture yet',
    sentence:
      'Nothing has been captured in this runtime session, by this tab or by any other. Press Observe to take one: it is one capture and one perception pass, and the robot does not move for it.',
    still: null,
    from: null,
    previous_seq: null,
  };
}

/**
 * THE VIDEO SENTENCE, kept apart from the still on purpose.
 *
 * `cameraReading` already decides what the camera SOURCE is doing and says it in the runtime's own words. What this
 * adds is the one distinction that was missing: whether a moving picture can be shown at all, said plainly, and said
 * in a way that can never be read as a statement about the still. The normal state of this baseline is that no live
 * video source is attached and stills are captured on demand, and an operator who reads "no continuous preview" beside
 * a picture has to be able to see immediately that the picture is fine and only the video is absent.
 *
 * The bare word "unavailable" is deliberately not used for a field here. It is this console's placeholder for a value
 * that could not be reported, and a state that IS reported must not be printed in the same word as one that was not.
 */
export function previewReading(camera: CameraReading, stillShown: boolean): { words: string; sentence: string } {
  if (camera.stream) {
    return {
      words: 'Continuous preview',
      sentence: 'A moving picture is being shown. It is for watching only: every result on this screen names the stored frame it was taken from.',
    };
  }
  const beside = stillShown
    ? ' A still IS present below and that is the ordinary case here: stills are captured on demand and are what every result on this screen rests on.'
    : '';
  return {
    words: 'No continuous preview',
    sentence: `No moving picture can be shown: there is no live video source attached to this runtime.${beside} ${camera.sentence}`,
  };
}
