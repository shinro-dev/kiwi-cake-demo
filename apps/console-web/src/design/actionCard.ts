// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CURRENT ACTION CARD, IN WORDS: one title and one sentence per fact.
//
// The card used to be composed by string concatenation, and on a live observe-only runtime it read
//
//   "The last run has finished; its duration and evidence are in History. the runtime was started without
//    --enable-hardware-commands"
//   "LIVE RUNTIME. failed."
//
// Three facts had been pasted into one line: that no run is going on, where the last recorded run's details are, and
// why a run cannot be started. The third arrived as the readiness blocker, which is a clause and not a sentence, so it
// began in lower case in the middle of the paragraph. The second line was the finished run's one-line summary, whose
// headline is the recorded outcome word when no measurement establishes more, so a failed run printed "failed." under
// its mode mark. That summary is History's now (the design review moved every completion statement there), and each
// fact here is its own sentence.
//
// NOTHING HERE INVENTS A RUN. "The newest run on record" is the runtime's own `last_outcome`: the newest finished run
// of this runtime's mode in its journal, which may be from an earlier runtime session on the same data directory. It is
// not called "this session's" run, and a runtime with no finished run gets no sentence about one.

import type { MissionWaiting } from '../model/missionView';
import type { RobotLink } from '../model/robotStatus';
import type { CurrentAction, Readiness } from '../model/steps';
import type { OutcomeView } from '../model/types';

export interface ActionCard {
  title: string;
  /** One sentence per fact, each starting with a capital and ending with a stop. Empty when there is nothing to add. */
  lines: string[];
}

/** A clause as a sentence: a capital first letter and a closing stop. A sentence already closed is left as it is. */
export function asSentence(text: string): string {
  const trimmed = text.trim();
  if (trimmed === '') return trimmed;
  const capital = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

/** What stands between the runtime and a run, as its own sentence, or null when nothing does. */
function blockerLine(ready: Readiness): string | null {
  if (ready.ready || ready.blocker === null) return null;
  // The runtime's clause, as it wrote it, closed with one stop.
  return `What stands in the way of a run: ${ready.blocker.trim().replace(/[.]+$/, '')}.`;
}

export function actionCard(input: {
  robotStale: boolean;
  link: Pick<RobotLink, 'state' | 'detail'>;
  waiting: MissionWaiting | null;
  running: boolean;
  result: OutcomeView | null;
  ready: Readiness;
  action: CurrentAction;
}): ActionCard {
  const { robotStale, link, waiting, running, result, ready, action } = input;
  // A ROBOT THAT IS NOT ANSWERING is said to be not answering, and never described by its last action.
  if (robotStale) {
    return {
      title: link.state === 'not_the_robot' ? 'Not the pinned build' : 'Robot not answering',
      lines: [asSentence(link.detail), 'Nothing on this screen about the robot is current until it answers again.'],
    };
  }
  if (waiting !== null && !running) {
    // THIS CARD IS THE CLASSIC SCREEN'S, which has no Try again control. The decision hold's recovery names that control,
    // so here it says where the choice is instead of offering a button this screen does not draw.
    const line =
      waiting.kind === 'capability'
        ? 'The capability request in the Cake panel is what lets the mission go on.'
        : waiting.kind === 'decision'
          ? waiting.offer === 'retry' || waiting.offer === 'move_block_then_retry'
            ? 'Nothing moves until you choose. The retry is offered on the default screen (open the console without ?screen=classic); Stop ends the mission.'
            : waiting.recovery
          : waiting.recovery;
    return { title: waiting.headline, lines: [line] };
  }
  const blocker = running ? null : blockerLine(ready);
  if (result !== null && !running) {
    const lines = [
      'No run is active.',
      "The newest run on this runtime's record has ended; its outcome, duration and evidence are in History.",
    ];
    if (blocker !== null) lines.push(blocker);
    return { title: ready.ready ? 'Idle, ready to run' : 'Idle', lines };
  }
  const lines: string[] = [];
  if (action.detail !== null) lines.push(asSentence(action.detail));
  else if (action.state !== null) lines.push(`Controller state ${action.state}.`);
  if (blocker !== null) lines.push(blocker);
  return { title: action.phrase, lines };
}
