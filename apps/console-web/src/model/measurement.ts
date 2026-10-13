// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CONTROLLER MEASUREMENT, AND THE THREE AGES THAT ARE NOT THE SAME AGE.
//
// This file exists because of two sentences that were on the screen at once. Under the heading "Measured" it said:
// "The host is not reporting measurements: tap_not_configured". Beside a readout that said "console: lines are
// arriving" it printed "7 min 59 s ago". A real non-motion Observe had just run on hardware, and while both of those
// were on the screen the runtime was holding a `measured.state` record of the whole pose the controller had read:
// six joints, a base velocity, and the source stamp of the host observation it read them from.
//
// TWO DIFFERENT THINGS WERE BEING CONFLATED.
//
//   THE CONTINUOUS TELEMETRY TAP is genuinely absent on this baseline and is deferred. `tap_not_configured` is the
//   runtime's true and complete answer about it.
//   A CONTROLLER MEASUREMENT WAS RECORDED. One `measured.state` record per command the controller executes, whether
//   or not anything moved. It has nothing to do with the tap, and printing the tap's absence in its place threw away
//   a measurement that had been taken.
//
// Both are now stated, each as itself, and the measurement never implies a feed: it is one record, with the time it
// was recorded and the source it was read from, and it says so in those words.
//
// THREE FRESHNESS FACTS, THREE LABELS, NEVER ONE WORD FOR TWO OF THEM. The contradiction on the screen was not a
// wrong number. It was two correct numbers about two different things printed as though they were one:
//
//   TRANSPORT ACTIVITY   when a line last arrived on the event stream. It is about the CONNECTION between this
//                        browser and the console API, and it says nothing whatever about the robot. Measured on this
//                        computer's clock, from the arrival stamp the store takes.
//   SOURCE MEASUREMENT   when the thing being measured was measured. For the baseline that is the controller
//                        measurement below, and its age is counted from the wall time the RUNTIME WROTE THE RECORD.
//   FRAME FRESHNESS      how old the picture is, which is a third thing again and lives with the camera card.
//
// The words below are the labels themselves, held in one place so that no two of these can drift into sharing one.
//
// WHY THE AGE IS COUNTED FROM A WALL CLOCK HERE AND FROM AN ARRIVAL STAMP ELSEWHERE. Transport activity is a fact
// about this browser and is measured with this browser's clock, which is the only honest way to measure it. The
// measurement's own age cannot be: nothing about it happened here. It carries the runtime's wall time, so the age is
// the difference between this browser's wall clock and that stamp, and the two clocks are NOT the same clock. That
// difference is stated as what it is, an approximate elapsed time between two machines, rather than presented at
// millisecond precision it does not have. It is still far better than the alternative that stood here for the frame,
// which was a count of records: "12 records ago" is a true statement about a log and not an answer to the question
// an operator is asking, which is how long ago.
//
// AND IT IS THE RECORDING TIME, NOT THE CAPTURE TIME. The wall stamp says when the runtime wrote the record. The
// instant the servo bus was sampled, or the shutter opened, is another clock on another computer, and relating them
// needs a clock relation this baseline does not supply. So wherever a time like this is printed, it is named as the
// recording time.

import { decideBySeq } from './merge';
import type { MergeSource } from './merge';
import type { MeasuredStateView, ViewState } from './types';

/** The three labels. They are separate facts and no two of them may ever share a word. */
export const TRANSPORT_ACTIVITY_LABEL = 'transport activity';
export const SOURCE_MEASUREMENT_LABEL = 'source measurement';
export const FRAME_FRESHNESS_LABEL = 'frame freshness';

/** What each label MEASURES, in one line, for the hover text beside it. */
export const AGE_MEANING: Record<string, string> = {
  [TRANSPORT_ACTIVITY_LABEL]: 'when a line last arrived on the event stream. It is about this connection, not about the robot.',
  [SOURCE_MEASUREMENT_LABEL]: 'when the thing being measured was measured, from the time the runtime recorded it.',
  [FRAME_FRESHNESS_LABEL]: 'how old the picture is, from the time the runtime recorded the capture.',
};

/**
 * How long ago a runtime wall stamp was, read against this browser's wall clock. Null when there is no stamp, which is
 * not an age of zero, and null for a stamp that cannot be parsed, which is not an age either.
 *
 * TWO CLOCKS. The stamp was written on the runtime's computer and the reading is taken here, so this is an elapsed
 * time ACROSS two clocks and carries whatever they disagree by. It is honest at the scale it is shown at, which is
 * seconds and minutes, and every caller says which stamp it is counting from. A stamp ahead of this clock produces a
 * negative difference, which is not a fact about anything, and it is reported as zero rather than as a negative age.
 */
export function ageFromWallUtc(wallUtc: string | null, nowMs: number): number | null {
  if (wallUtc === null) return null;
  const parsed = Date.parse(wallUtc);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, nowMs - parsed);
}

export interface MeasurementReading {
  /** The measurement to draw, or null when neither source holds one. */
  state: MeasuredStateView | null;
  /** Which source it was decided from, so the claim can be checked against the record. Null when there is none. */
  from: MergeSource | null;
  /** How long ago the runtime RECORDED it, across the two clocks. Null when the record carries no wall time. */
  age_ms: number | null;
  /** The heading. It never says "measured" on its own, which is the word the absent tap had taken. */
  words: string;
  /** The whole sentence: what this is, where it came from, and what it is not. */
  sentence: string;
}

/** The heading the panel carries. It names a record, not a feed. */
export const MEASUREMENT_TITLE = 'Last controller measurement';

/**
 * THE MEASUREMENT THE RUNTIME HAS, decided between the fold and the snapshot by sequence number.
 *
 * Both sources describe records of ONE ordered log of one runtime session, and the reducer clears the folded half
 * whenever a snapshot names a different session, so what reaches here is always two descriptions of the same log.
 * `decideBySeq` then takes the later record, in both directions: a snapshot delayed in flight cannot put an older
 * reading back on the screen, and a record folded long ago cannot hide the newer one a snapshot is naming.
 */
export function measurementReading(view: ViewState, nowMs: number, snapshotRead = true): MeasurementReading {
  const decided = decideBySeq(view.measured_state, view.snapshot_measured_state);
  const state = decided.value;
  if (state === null && !snapshotRead) {
    // NOTHING WAS READ (verify-r1 must_fix 3): no record says whether a controller read the robot, and none is claimed.
    return {
      state: null,
      from: null,
      age_ms: null,
      words: 'Not read',
      sentence:
        'Not read: no snapshot from the runtime has reached this page, so whether a controller has read the robot is not known here. This is a statement about the records, not about the continuous telemetry tap, which is reported separately below.',
    };
  }
  if (state === null) {
    return {
      state: null,
      from: null,
      age_ms: null,
      words: 'No controller measurement yet',
      sentence:
        'No controller has read the robot in this runtime session, so there is no pose recorded here to show. This is a statement about the records, not about the continuous telemetry tap, which is reported separately below.',
    };
  }
  const origin =
    decided.from === 'folded'
      ? 'this tab folded the record off the event stream'
      : 'the runtime named it on the snapshot, because this tab was opened or reloaded after the command and folded no record for it';
  return {
    state,
    from: decided.from,
    age_ms: ageFromWallUtc(state.t_wall_utc, nowMs),
    words: 'One recorded pose',
    sentence: `The pose below is the last one a controller read and recorded, at record ${String(state.seq)}: ${origin}. It is ONE record, written when the controller read the robot. It is not a live feed and nothing here refreshes on its own.`,
  };
}
