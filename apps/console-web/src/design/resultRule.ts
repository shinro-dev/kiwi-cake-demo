// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT COLOUR THE RULE DOWN THE SIDE OF THE RESULT CARD IS, and what each colour is a claim about.
//
// This was one conditional inside the card's JSX, and it read `result !== null ? C.ok : ...`: EVERY finished run got
// the colour this screen uses for a good outcome, including the ones the runtime reported as failed, stopped or
// refused. Run console-20260922T194137Z ended `failed` with exit 5 and drew a green rule. A colour is the fastest
// thing on a screen to read and the slowest to doubt, so it is the worst place to say the opposite of the record.
//
// It is a function here, and not a conditional in the markup, for the same reason every other decision on this screen
// is: the rule is a claim about a run, so it can be asked a question in a test without rendering anything.
//
// TWO FURTHER DISTINCTIONS THE OLD CONDITIONAL DID NOT DRAW:
//
//   A RUN IN PROGRESS IS NOT AN IDLE CONSOLE THAT COULD START ONE. Both were `C.accent`. "The robot is moving" and
//   "nothing is running and the robot says it is ready" are the two states somebody watching most needs to tell
//   apart, and they were the same colour.
//   AN IDLE RUNTIME IS NOT AN ESTABLISHED READINESS. `ready` here is the runtime's own answer about the robot
//   computer; a console with no answer is drawn as the neutral rule and never as a ready one.
//
// The runtime's word is what decides, and this file never recomputes an outcome: `BAD_OUTCOMES` is the same set
// `model/command.ts` already reads, and a reason code on a finished run is the controller saying why it did not end
// as it should.

import type { OutcomeView } from '../model/types';
import { C, INK } from './theme';

/** What the rule is saying, independent of which hex the theme gives it. The words a test asks about. */
export type ResultRule = 'failed' | 'succeeded' | 'running' | 'ready' | 'idle' | 'blocked';

/**
 * The outcomes the RUNTIME reports for a run that did not end well. The same three words `model/command.ts` reads,
 * and for the same reason: `unknown` is not one of them, because a command with no placement in it has no placement
 * for the runtime to name the outcome after and it writes exactly that.
 */
const BAD_OUTCOMES: ReadonlySet<string> = new Set(['failed', 'stopped', 'refused']);

/**
 * Whether a finished run ended badly, from the record alone.
 *
 * Three independent statements, any one of which is the run saying it did not end well: the reported outcome word, a
 * non-zero exit status, and a reason code. A reason code on a finished run is the controller explaining why it did
 * not end as it should, so a present one is never read past, which is the rule `nonPlacementCommandSucceeded`
 * already applies to the same record.
 */
export function resultFailed(result: OutcomeView): boolean {
  if (result.reported !== null && BAD_OUTCOMES.has(result.reported)) return true;
  if (result.exit_status !== null && result.exit_status !== 0) return true;
  return result.reason_code !== null;
}

/**
 * What the result card's rule states.
 *
 * A finished run answers first and answers for itself, because it is the newest fact on the card. Only when there is
 * no finished run does the console's own state decide, and there the four cases stay four: something is moving,
 * nothing is moving but the robot answered ready, nothing is moving and nobody has asked for anything, and something
 * is standing in the way.
 */
export function resultRule(input: {
  result: OutcomeView | null;
  running: boolean;
  ready: boolean;
  blocked: boolean;
}): ResultRule {
  if (input.result !== null) return resultFailed(input.result) ? 'failed' : 'succeeded';
  if (input.running) return 'running';
  if (input.blocked) return 'blocked';
  if (input.ready) return 'ready';
  return 'idle';
}

/**
 * The colour for each. `C.bad` for a failure is the same red every other failed thing on this screen uses, so a
 * failed run reads the same whether it is met on the card, in the phase rail or in the failure panel.
 */
export const RESULT_RULE_COLOUR: Record<ResultRule, string> = {
  failed: C.bad,
  succeeded: C.ok,
  running: C.accent,
  ready: C.ok,
  idle: INK.rule,
  blocked: C.wait,
};

export function resultRuleColour(input: {
  result: OutcomeView | null;
  running: boolean;
  ready: boolean;
  blocked: boolean;
}): string {
  return RESULT_RULE_COLOUR[resultRule(input)];
}
