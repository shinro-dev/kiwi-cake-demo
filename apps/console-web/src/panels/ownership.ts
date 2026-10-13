// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT OWNS THE ROBOT WHILE A RUN IS GOING, in the words the operator reads, from the runtime's own statement and
// from nothing else.
//
// TWO QUESTIONS, AND THE SECOND IS THE ONE THAT IS EASY TO OVERSTATE. Which path a command takes to the robot is one
// fact; what stands between this run and anybody else commanding the same robot is another. The contract keeps them
// apart (`snapshot.execution`), and so does this module, because the sentence that would be wrong here is the
// comfortable one: that something is holding the robot for us.
//
// UNDER THE BASELINE BACKEND NOTHING ON THE ROBOT IS REFUSING ANYTHING. The runtime runs the proven controller
// against the motor host in its normal mode and takes no lease of any kind. The whole of the ownership is this
// runtime's own single run slot, on the laptop, enforced by the laptop: it refuses a second run here, and that is
// all it does. Any other process on the robot's computer can still open the servo bus, and a person at that computer
// can still start anything. The screen says exactly that.
//
// THE WORDS "LEASE HELD" ARE NEVER PRINTED FOR THE BASELINE BACKEND. Not as a no, not as a not-applicable, not in a
// tooltip. A screen that prints the phrase at all teaches the reader that a lease is the thing being reported on,
// and the reading that follows from a greyed-out "lease held: no" is that a lease would be held if things were
// working, which is the opposite of the truth: none was ever asked for.
//
// AN ABSENT STATEMENT IS NOT AN ANSWER. A runtime that sends no `execution` at all, and a replay, which is folded
// from events and carries none, have said nothing about either question. That is drawn as a question nothing has
// answered, and it is never filled in from the lease beside it.

import type { SnapshotExecution } from '@kiwi/demo-contracts';
import type { BadgeTone } from '../ui/Badge';

/** The runtime's name for the path that runs the proven controller against the motor host in its normal mode. */
export const BASELINE_BACKEND = 'baseline_normal_host';

/** The runtime's name for the path that needs the robot-side host boundary and its command lease. */
export const BOUNDARY_BACKEND = 'boundary_lease_host';

/**
 * THE SAME TWO ANSWERS AT TWO LENGTHS.
 *
 * The strip is sticky and every row it takes is a row the current action and the phase do not get, so what goes there
 * is a clause that is complete on its own and still fits: `basis` and `robotShort`. The engineering view has the
 * room, and gets the whole finding in `robotSide` plus the runtime's own sentence in `detail`.
 *
 * SHORTER IS NOT SOFTER. The short form of "nothing on the robot refuses anything" says the same thing as the long
 * one; what the long one adds is the consequence, which is that any other process on that computer can still open the
 * servo bus. Neither is allowed to become "no lease", and neither is allowed to become a hopeful "not required".
 */
export interface OwnershipWords {
  /** The backend, short enough for the strip. Never the bare enum value, which reads as a log line. */
  backend: string;
  /** What owns the robot, in one short clause for the strip. */
  basis: string;
  /** Whether anything ON THE ROBOT is refusing commands, in a clause short enough for the strip. */
  robotShort: string;
  /**
   * The same finding in full, with its consequence, for the engineering view. This is the sentence that must not be
   * softened: under the baseline backend the answer is no, stated as a finding and not as an unknown.
   */
  robotSide: string;
  /** The tone the ownership badge takes. Nothing here is a fault, so nothing here is ever drawn as one. */
  tone: BadgeTone;
  /** The runtime's own full sentence, for the hover and for the engineering view. */
  detail: string;
  /** True when this runtime has said nothing at all about either question. */
  unstated: boolean;
}

/** What a runtime that has stated nothing gets. It is a question nobody answered, and it is not a fault. */
const UNSTATED: OwnershipWords = {
  backend: 'not stated',
  basis: 'this runtime has not said what owns the robot',
  robotShort: 'nothing has answered it',
  robotSide: 'whether anything on the robot refuses another command is not established, because nothing has answered it',
  tone: 'quiet',
  detail:
    'This runtime states no execution backend and no ownership basis. That is not the same as there being no ownership, and it is not read off the lease beside it: a runtime on the baseline backend holds no lease on purpose, so an absent lease says nothing about either question.',
  unstated: false,
};

/**
 * The ownership, in words, exactly as the runtime stated it.
 *
 * The three pieces are built from the two enumerated fields rather than from the sentence, so a runtime whose wording
 * changes cannot change what the screen claims; the runtime's own sentence is carried beside them, unaltered, as the
 * detail.
 */
export function ownershipWords(execution: SnapshotExecution | null): OwnershipWords {
  if (execution === null) return { ...UNSTATED, unstated: true };

  const baseline = execution.backend === BASELINE_BACKEND;
  const backend = baseline ? 'baseline: the normal host' : 'boundary: the host lease';

  const basis =
    execution.ownership_basis === 'runtime_single_run_slot'
      ? 'one run at a time, refused here'
      : 'a command lease the robot-side boundary granted';

  // false is a FINDING and is said as one. null is nothing having answered, and the two are never merged: "no" and
  // "nobody asked" are different things to do something about.
  const robotShort =
    execution.robot_side_enforcement === false
      ? 'nothing on the robot refuses anything'
      : execution.robot_side_enforcement === true
        ? 'the robot-side boundary is refusing commands'
        : 'nothing has answered it';

  const robotSide =
    execution.robot_side_enforcement === false
      ? 'nothing on the robot is refusing a command from anywhere else, and any other process on that computer can still open the servo bus'
      : execution.robot_side_enforcement === true
        ? 'the robot-side boundary is refusing commands that do not carry this ownership'
        : 'whether anything on the robot refuses another command is not established, because nothing has answered it';

  return {
    backend,
    basis,
    robotShort,
    robotSide,
    // Neither backend is a fault and neither is a success. The baseline is the state this milestone runs in by
    // design, so it takes the neutral tone rather than a warning that would send somebody looking for a problem.
    tone: baseline ? 'quiet' : 'info',
    detail: execution.detail,
    unstated: false,
  };
}

/**
 * Whether a lease may be spoken about on this screen at all.
 *
 * Under the baseline backend it may not, in any form: see the note at the top of this file. Under the boundary
 * backend the lease is the ownership, so it is reported in full.
 */
export function leaseIsTheSubject(execution: SnapshotExecution | null): boolean {
  return execution !== null && execution.backend === BOUNDARY_BACKEND;
}
