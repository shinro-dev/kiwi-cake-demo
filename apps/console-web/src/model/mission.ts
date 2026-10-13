// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE THREE FIGURES THE IMPORTED DESIGN PUTS BESIDE THE PHASE RAIL: target, progress and mission time.
//
// The design was drawn over a two-cube prototype whose state was a literal (`elapsed: 26`, `placed: 0`,
// `target: 'Cube A'`, a `setInterval` adding one a second). None of those three quantities existed in this console
// before, so each one is derived HERE, from records, with the case where the record does not support an answer
// returning null rather than a number. THE TASK IS ONE CUBE. Nothing in this file counts to two.
//
// WHAT EACH ONE RESTS ON, and why none of them is the obvious thing:
//
//   MISSION TIME is not a counter. Nothing on this path holds one: `SnapshotActiveRun` carries `started_seq`, which is
//   a position in a log and not a clock, and a sequence number cannot be subtracted into a duration. The only clock
//   reading that dates a run is the wall time on its own `run.started` envelope, so that is what is folded
//   (`model/reducer.ts`, `run_started.started_wall_utc`) and what is measured against here. A FINISHED run is measured
//   start-to-finish inside the RUNTIME's own clock, which is a subtraction of two readings of one clock. A run still
//   going is measured against this browser's clock, which crosses two of them, and that is said in the words the
//   screen shows rather than hidden. A tab that joined mid-run folded no `run.started` at all and gets null, which the
//   design's own binding table already calls for a stated fallback rather than a number. The design writes that
//   fallback as a dash; this console writes the word `unavailable`, because `tests/test-docs.sh` forbids an em-dash in
//   any committed file and because the word is what every other absent figure on this screen already says.
//
//   TARGET is not a name. With two cubes a target is an identity worth printing; with one it is always the same cube,
//   and printing "Cube A" would be a label with no information in it. What varies, and what somebody watching wants,
//   is where that one cube has got to, so the target reads as the cube's own recorded state.
//
//   PROGRESS is not a count of what has not happened yet. `placed` is a predicate the controller evaluates, so an
//   absent verdict means one of two different things: within a run this tab watched from the start, nothing has been
//   placed (0 is a fact); in a tab that joined mid-run, this tab simply did not see it (null, not 0). That is the same
//   distinction `model/phase.ts` draws with `not_observed`, and it is drawn the same way here.

import { ageFromWallUtc } from './measurement';
import { predicateState } from './predicate';
import { resultForCurrentRun, runIsActive } from './steps';
import type { PredicateView, ViewState } from './types';


/** mm:ss, the way the design writes a mission clock. Hours are carried rather than wrapped silently at 60 minutes. */
export function clockWords(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');
  return hours > 0 ? `${String(hours)}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** The predicate record for one name, or undefined. Only an EVALUATED record answers; the other three states do not. */
function verdict(predicates: PredicateView[], name: string): boolean | null {
  const record = predicates.find((item) => item.predicate === name);
  if (record === undefined) return null;
  if (predicateState(record) !== 'evaluated') return null;
  return record.value;
}

export interface MissionTime {
  /** mm:ss, or null when nothing dates this run. Null prints as the word `unavailable` on the screen, never as 00:00. */
  words: string | null;
  /** What the figure is a measurement of, for the title attribute. Always present, including for the null case. */
  meaning: string;
  /** True while it is still growing, so the screen knows this one advances on the shared tick. */
  running: boolean;
}

/**
 * How long the run on the screen has been going, or took.
 *
 * The two cases are measured differently ON PURPOSE and the words say which is which, because one of them crosses two
 * computers' clocks and the other does not.
 */
export function missionTime(view: ViewState, nowMs: number): MissionTime {
  const started = view.run_started?.started_wall_utc ?? null;
  const result = resultForCurrentRun(view);

  if (started === null) {
    return {
      words: null,
      meaning:
        view.active_run === null
          ? 'No run has started in this tab, so there is no run to time.'
          : 'This tab joined after the run began, so it never folded the record that says when the run started. That is a missing start time, not an elapsed time of zero.',
      running: false,
    };
  }

  const finishedAt = result?.finished_wall_utc ?? null;
  if (finishedAt !== null) {
    const from = Date.parse(started);
    const to = Date.parse(finishedAt);
    if (Number.isFinite(from) && Number.isFinite(to) && to >= from) {
      return {
        words: clockWords(to - from),
        meaning: "Start to finish, both read from the runtime's own wall clock on the run's own records.",
        running: false,
      };
    }
  }

  const ageMs = ageFromWallUtc(started, nowMs);
  if (ageMs === null) {
    return { words: null, meaning: 'The record that started this run carries no time that can be read.', running: false };
  }
  const running = runIsActive(view);
  return {
    words: clockWords(ageMs),
    meaning: running
      ? "Since the run.started record's own wall time, measured against this computer's clock. The two clocks are on different machines and nothing here has measured their relation."
      : "Since the run.started record's own wall time. The run is no longer active and no record states when it finished, so this is time since it began and not its duration.",
    running,
  };
}

export type CubeState = 'not_observed' | 'not_located' | 'located' | 'held' | 'placed';

export interface MissionTarget {
  /** What the screen prints under "Target". */
  label: string;
  state: CubeState;
  /** The record the state rests on, for the title attribute. */
  basis: string;
  /** True while the mission is held rather than moving, which the design tints differently. */
  waiting: boolean;
}

/**
 * THE ONE CUBE, and where the records say it has got to.
 *
 * `cube_seen_with_a_contact_point` is the asymmetric one and is read accordingly: `model/phase.ts` records that the
 * controller emits it ONLY when the search failed, so a true value for it is never available and its presence is
 * read as a failure to locate and never as a success.
 */
/**
 * `predicates` is the SELECTED RUN's resolved predicate list, the same one the result and the progress figure read,
 * so the three cannot describe different runs. `liveFold` says whether the live fold still describes that run: for a
 * finished run it does not, and the perception fallback below, which is a statement about this session, is then not
 * consulted at all. Both default to the old behaviour so a caller that has no run in hand is unchanged.
 */
export function missionTarget(view: ViewState, predicates: PredicateView[] = view.predicates, liveFold = true): MissionTarget {
  const placed = verdict(predicates, 'placed');
  if (placed === true) {
    return { label: 'placed on the pad', state: 'placed', basis: 'the placed predicate measured true', waiting: false };
  }
  const secured = verdict(predicates, 'object_secured');
  if (secured === true) {
    return { label: 'held by the gripper', state: 'held', basis: 'the object_secured predicate measured true', waiting: false };
  }
  const seen = verdict(predicates, 'cube_seen_with_a_contact_point');
  if (seen === false) {
    return {
      label: 'not located',
      state: 'not_located',
      basis: 'the controller recorded cube_seen_with_a_contact_point as false, which it emits only when the search failed',
      waiting: true,
    };
  }
  // THE FALLBACKS BELOW DESCRIBE THIS SESSION, NOT A FINISHED RUN. The newest perception record and this tab's own
  // fold are statements about what is happening now; under a run that has ended, or one the runtime has moved on
  // from, they would report the session's state under that run's name. The record for such a run is its predicates,
  // and those were read above.
  if (!liveFold) {
    return {
      label: 'not recorded for this run',
      state: 'not_observed',
      basis: "this run has ended and its own records do not say where the cube finished; the live fold describes the session, not this run",
      waiting: false,
    };
  }
  // A perception that actually detected something is the positive evidence the locate phase never records for itself.
  const detected = view.last_perception?.payload.result ?? null;
  if (detected === 'detected') {
    return { label: 'located', state: 'located', basis: 'the newest perception.result record reports detected', waiting: false };
  }
  const joinedMidRun = view.active_run !== null && view.run_started === null;
  if (joinedMidRun) {
    return {
      label: 'not seen from here',
      state: 'not_observed',
      basis: 'this tab joined after the run began and folded no record of where the cube is',
      waiting: false,
    };
  }
  return {
    label: 'not located yet',
    state: 'not_located',
    basis: 'no perception record in this fold reports a detection',
    waiting: false,
  };
}

export interface MissionProgress {
  /** The sentence under "Progress". One cube, always. */
  words: string;
  /** How many of the one cube are placed, or null when this tab cannot say. Null is never drawn as a zero. */
  placed: number | null;
}

/**
 * HOW MUCH OF THE ONE-CUBE TASK IS DONE.
 *
 * Zero is a claim and it is only made where the record supports it: inside a run this tab watched from its start, or
 * after a run that finished without placing. A tab that joined mid-run says it cannot tell, in the same words
 * `model/phase.ts` uses for the same reason.
 */
/**
 * `predicates` is the run's RESOLVED predicate list: the live fold when this tab has it, and otherwise the records read
 * back from the run's own retained evidence. `evidence` says which of those three states the read-back is in, so the
 * screen can tell "still reading" from "the runtime has no such record", instead of reporting this tab's fold as though
 * it were the run.
 */
export type EvidenceState = 'reading' | 'retained' | 'unavailable';

export function missionProgress(view: ViewState, predicates: PredicateView[] = view.predicates,
                                evidence: EvidenceState = 'retained', placesACube = true): MissionProgress {
  const placed = verdict(predicates, 'placed');
  if (placed === true) return { words: '1 of 1 cube placed', placed: 1 };
  if (placed === false) return { words: '0 of 1 cube placed', placed: 0 };

  const joinedMidRun = view.active_run !== null && view.run_started === null;
  if (joinedMidRun) return { words: '1 cube, not seen from here', placed: null };

  // A COMMAND THAT PLACES NOTHING IS NOT A MISSING PLACEMENT. An Observe and a return of the arm run through the same
  // supervisor and end with the same kind of record, and reporting "the placement was not recorded" after one of them
  // describes a placement nobody asked for.
  if (!placesACube) return { words: '1 cube, this command places nothing', placed: null };

  const result = resultForCurrentRun(view);
  if (result !== null) {
    // A finished run whose placement verdict has not resolved. WHICH of the two it is matters: one of them becomes an
    // answer in a moment and the other never will, and "not in this fold" said neither, because it described this
    // tab's bookkeeping rather than the run.
    if (evidence === 'reading') return { words: '1 cube, reading the run evidence', placed: null };
    return { words: '1 cube, placement not recorded for this run', placed: null };
  }
  if (view.active_run !== null || view.machine !== null) return { words: '0 of 1 cube placed', placed: 0 };
  return { words: '1 cube, no run in this session', placed: null };
}
