// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT THIS CONSOLE MAY SAY ABOUT THE ROBOT, which is a great deal less than what it may say about the runtime.
//
// Everything else on this screen is a fact about a process on this laptop: the runtime answered, the capability list
// says this, a record was folded. This is about a computer at the other end of a network that this browser has never
// spoken to and never will. The only thing that ever reaches here is a reading the runtime took in the background,
// sent inside a snapshot, and named with one of six reason codes on the capabilities it turned off.
//
// THREE DIFFERENT THINGS ARE KEPT APART HERE, and merging any two of them is how a screen ends up drawing a ready
// robot that is not there:
//
//   AN ANSWER     the robot computer was asked and something came back. Four of the codes are that: it could not be
//                 reached, no motor host is running on it, a host is running in scan mode, the mode could not be
//                 read. Those are four different next actions for the person reading this, so they are four
//                 different sentences and they are never collapsed into one word such as "not ready".
//   NO ANSWER     the runtime has nothing to report, either because no reading has come back yet in this runtime
//                 session or because the last one is too old to stand for the robot now. That is not a robot that is
//                 ready and it is not a robot that is broken, and it is drawn as neither.
//   NOT ASKED     the control is off for something this laptop decided before it ever looked at the robot, or this
//                 runtime reads nothing about a robot at all. Nothing here is then a statement about the robot.
//
// STALENESS ONLY EVER TAKES READINESS AWAY. The runtime ages its own reading out and reports what is left as no
// answer at all. This console adds one age of its own, because a snapshot that stopped arriving leaves the last
// capability list on the screen for as long as the tab stays open: an answer that said a motor host is running stops
// being offered as one once the snapshot that carried it is older than ROBOT_ANSWER_STALE_AFTER_MS. A blocker is NOT
// aged out the same way, and the asymmetry is deliberate. An old refusal is still not a green light, and forgetting
// it as it ages would turn a stale screen into a ready-looking one, which is the one failure this module exists to
// prevent.
//
// AND A PASSING CHECK IS NOT AN ATTACHMENT: see ATTACH_IS_DECIDED_ON_THE_ROBOT, which is on the screen beside the
// control that starts motion and not only in a document.

import type { Capability } from '@kiwi/demo-contracts';
import { freshness } from './format';
import type { ViewState } from './types';

/**
 * The dependency the runtime names on a capability it turned off because of the ROBOT rather than because of anything
 * on this laptop. It is the runtime's own word (`read_models.capabilities`), and it is what tells a robot block apart
 * from a preflight block that happens to carry a similar sentence.
 */
export const ROBOT_DEPENDENCY = 'robot';

/**
 * The runtime's name for the path that runs the proven controller against the motor host in its normal mode.
 *
 * `panels/ownership.ts` names the same token for the strip, and the two are held equal by a test rather than imported
 * across the layers: this module is read by the model and that one is written for the strip, and neither should have
 * to load the other to agree on one word the runtime chose.
 */
export const BASELINE_BACKEND = 'baseline_normal_host';

/**
 * How long a snapshot carrying an answer about the robot stands before this console stops reading that answer as a
 * statement about the robot NOW.
 *
 * The runtime is polled every five seconds, so this is three polls that did not arrive. It is a display and refusal
 * threshold of this console's own, like the one on the lease, and it is stated on the screen beside what it refuses.
 * It is deliberately shorter than the runtime's own limit on its reading: the two ages are measured on two different
 * clocks, about two different things, and they are never added together.
 */
export const ROBOT_ANSWER_STALE_AFTER_MS = 15_000;

interface RobotCode {
  /** True when the robot computer SAID this. False for the two codes that mean nobody said anything. */
  answered: boolean;
  /** The blocker in a few words, different enough from its neighbours to be read at a glance. */
  headline: string;
  /** What follows from it, which is different for every one of them. It never restates the runtime's own sentence. */
  consequence: string;
}

/**
 * The six codes the runtime writes on a capability it turned off because of the robot, each with the words this
 * console puts on the screen for it.
 *
 * The literals mirror `demo_runtime.preflight`, which owns them, and a test holds this table against the runtime's
 * own `ROBOT_BLOCK_STATES` so that a code renamed there cannot quietly stop being recognized here. The runtime's
 * sentence travels with the block and is shown unaltered beside these; nothing here rewrites it or shortens it.
 */
const ROBOT_CODES: Record<string, RobotCode> = {
  motor_host_unreachable: {
    answered: true,
    headline: 'the robot computer could not be reached',
    consequence:
      'nothing answered on the way to the robot computer, so what has to be established next is whether it is powered and whether this laptop has a route to it. This is NOT the same finding as a robot computer that answered and has no motor host running on it.',
  },
  motor_host_not_running: {
    answered: true,
    headline: 'no motor host is running on the robot computer',
    consequence:
      'the robot computer answered, so it is reachable. What it answered is that nothing is serving the servo bus on it, and the proven host has to be running there before a cycle can be commanded.',
  },
  motor_host_in_scan_mode: {
    answered: true,
    headline: 'the motor host is running in scan mode',
    consequence:
      'scan mode leaves the gripper limp and strips every gripper key out of an action, so a grasp would close on nothing. The host has to be running in its normal mode.',
  },
  motor_host_mode_unknown: {
    answered: true,
    headline: "the motor host's mode could not be read",
    consequence:
      'the robot computer answered, but what is running on it could not be established, so it is treated as a host in neither mode rather than as one in the mode that would be convenient.',
  },
  robot_readiness_not_read: {
    answered: false,
    headline: 'nothing has been read about the robot yet',
    consequence:
      'no reading has come back in this runtime session yet. The first one arrives within a few seconds of the runtime starting, and until it does this says nothing about the robot in either direction.',
  },
  robot_readiness_stale: {
    answered: false,
    headline: 'the last reading is too old to stand for the robot now',
    consequence:
      'the runtime reports an answer older than its own limit as no answer at all rather than as the answer it used to be, so there is nothing here that describes the robot now.',
  },
};

/** The codes this console draws. A test holds the list against the runtime's own table. */
export const ROBOT_REASON_CODES: readonly string[] = Object.keys(ROBOT_CODES);

/** The headline for one of those codes, or null for a code that is not one of them. */
export function robotHeadline(code: string | null): string | null {
  if (code === null) return null;
  return ROBOT_CODES[code]?.headline ?? null;
}

/**
 * THE SENTENCE THE OPERATOR ASKED FOR, IN THE PLACE WHERE SOMEBODY IS ABOUT TO PRESS START.
 *
 * A source verification is a check on this laptop against a copy of what is installed on the robot computer. It can
 * pass while the very next thing the host does is refuse to attach the arm, and a power cycle is the ordinary way to
 * arrive at exactly that: `bin/pi/lekiwi_host_safe_attach.py` refuses while torque is off, and it refuses while a
 * joint's goal and present positions are further apart than its tolerance of about one degree, and a power cycle
 * turns torque off and resets every goal position while the arm stays physically where it is. Nothing this console
 * can read says whether the arm has been acquired since, so this screen never says or implies that it has.
 */
export const ATTACH_IS_DECIDED_ON_THE_ROBOT =
  "A source verification that passes says the host source that was checked is the proven one. It is not a promise that the host will attach the arm. Attachment is decided afterwards, on the robot, against the live registers: the safe-attach host refuses while torque is off, and it refuses while a joint's goal and present positions are further apart than its own tolerance of about one degree. A power cycle leaves a healthy host in exactly that state, so the arm has to be acquired first, and nothing this console can read says whether it has been.";

/** What this console has about the robot right now, and what it is allowed to draw from it. */
export type RobotState =
  /** This runtime reads nothing about a robot at all, so there is nothing here to report in either direction. */
  | 'nothing_is_read'
  /** A control is off for something decided before the robot was looked at, so the robot was never asked. */
  | 'not_asked'
  /** The robot computer answered, and the answer is a refusal. */
  | 'answered_blocked'
  /** Nobody has said anything: nothing read yet, or a reading the runtime aged out. */
  | 'no_answer'
  /** An answer that said a motor host is running, in a snapshot too old for this console to stand behind. */
  | 'answer_not_current'
  /** The robot computer answered that a motor host is running and is not in scan mode. */
  | 'host_running';

export interface RobotReading {
  state: RobotState;
  /** The runtime's own reason code, when the block carried one. Null when there is no block to carry one. */
  code: string | null;
  /** The state in a few words, for the badge. Never a colour on its own. */
  headline: string;
  /** The RUNTIME's sentence, unaltered and unshortened. Null when the runtime sent none. */
  runtimeDetail: string | null;
  /** What this console says follows from it. It never restates the runtime's sentence and never softens it. */
  consequence: string;
  /**
   * True ONLY when the robot computer answered that a motor host is running and the snapshot that carried the answer
   * is still current here. Even then it is not a statement that a command will be accepted: see
   * ATTACH_IS_DECIDED_ON_THE_ROBOT, and the runtime asks the robot computer again, fresh, on every submit.
   */
  offered: boolean;
  /** When the snapshot this rests on reached THIS browser, for the shared age primitive. Null when none has. */
  atMs: number | null;
}

/** The capability the runtime turned off because of the robot, or null when none is. */
export function robotBlockedCapability(view: ViewState): Capability | null {
  for (const capability of view.capabilities) {
    const code = capability.reason_code;
    if (capability.dependency === ROBOT_DEPENDENCY && code !== null && code in ROBOT_CODES) return capability;
  }
  return null;
}

function capabilityById(view: ViewState, id: string): Capability | null {
  return view.capabilities.find((capability) => capability.id === id) ?? null;
}

/** The runtime's sentence as it arrived, or null for an empty one. An empty string is not a sentence. */
function runtimeSentence(capability: Capability): string | null {
  const reason = capability.reason;
  return reason === null || reason === '' ? null : reason;
}

/**
 * What this console has about the robot, read out of the capability list and out of nothing else.
 *
 * `nowMs` is the shared reading of this computer's clock, so that the age of an answer advances on its own rather
 * than at whatever moment something else causes a render.
 */
export function robotReading(view: ViewState, nowMs: number): RobotReading {
  const atMs = view.snapshot_at_ms;
  const base = { code: null, runtimeDetail: null, offered: false, atMs };

  // A runtime that reads nothing about a robot. A simulation has no robot to be ready; a runtime that has not stated
  // the baseline backend has not said that it runs commands through the proven motor host, and this console reads a
  // robot computer under no other path. Neither is drawn as a robot that is waiting for something.
  if (view.data_mode !== 'live') {
    return {
      ...base,
      state: 'nothing_is_read',
      headline: 'nothing about a robot is read here',
      consequence:
        'this runtime does not report itself as live, so there is no robot computer being asked about and nothing on this screen says whether one is ready.',
    };
  }
  if (view.execution === null || view.execution.backend !== BASELINE_BACKEND) {
    return {
      ...base,
      state: 'nothing_is_read',
      headline: 'nothing about a robot is read here',
      consequence:
        'this runtime has not stated that it runs commands through the proven motor host in its normal mode, and this console reads a robot computer under no other path, so nothing here says whether one is ready.',
    };
  }

  const blocked = robotBlockedCapability(view);
  if (blocked !== null) {
    const code = blocked.reason_code ?? '';
    const entry = ROBOT_CODES[code];
    if (entry !== undefined) {
      return {
        ...base,
        state: entry.answered ? 'answered_blocked' : 'no_answer',
        code,
        headline: entry.headline,
        runtimeDetail: runtimeSentence(blocked),
        consequence: entry.consequence,
        offered: false,
      };
    }
  }

  // No robot block on the list. That is either an answer that a motor host is running, or a list that was turned off
  // earlier for something else, in which case the robot was never reached: the runtime decides the local refusals
  // first and only then looks at its reading. The difference matters, because the second is not a ready robot.
  const start = capabilityById(view, 'start_cycle');
  if (start === null) {
    return {
      ...base,
      state: 'not_asked',
      headline: 'the robot was not asked about',
      consequence: 'this runtime does not report the control that would start a cycle, so there is nothing here that says anything about the robot.',
    };
  }
  if (start.state !== 'enabled') {
    return {
      ...base,
      state: 'not_asked',
      code: start.reason_code,
      headline: 'the robot was not asked about',
      runtimeDetail: runtimeSentence(start),
      consequence:
        'the control that would start a cycle is off for something this runtime decides before it looks at the robot, so nothing here is a statement about the robot in either direction.',
    };
  }

  // An answer, and then the one question this console adds to it: did that answer arrive recently enough to stand for
  // the robot now? An answer nobody has refreshed is not a current answer, and it is never drawn as readiness.
  const ageMs = atMs === null ? null : nowMs - atMs;
  if (atMs === null || freshness(ageMs, ROBOT_ANSWER_STALE_AFTER_MS) !== 'fresh') {
    return {
      ...base,
      state: 'answer_not_current',
      headline: 'the answer about the robot is not current',
      consequence:
        atMs === null
          ? 'no snapshot has reached this browser yet, so there is no answer here to read about the robot.'
          : `the snapshot carrying the last answer about the robot reached this browser longer ago than the ${String(
              ROBOT_ANSWER_STALE_AFTER_MS / 1000,
            )} s this console counts as current, so what it said is not read here as a statement about the robot now.`,
    };
  }

  return {
    ...base,
    state: 'host_running',
    headline: 'the robot computer answered that a motor host is running',
    offered: true,
    consequence:
      'the robot computer answered this runtime that a motor host is running on it and is not in scan mode. A start does not rest on that reading: the runtime asks the robot computer again, fresh, when Start is pressed, and refuses there if the answer has changed since.',
  };
}
