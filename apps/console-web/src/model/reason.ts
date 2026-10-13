// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Why the controller is doing what it is doing, in a sentence, built by TEMPLATE from recorded facts: the controller's
// own state name, the action it planned with its parameters, the reason code it refused with. No model is consulted,
// nothing is summarized and nothing is inferred. A state or a reason code this file does not know is printed as it
// arrived, next to the server's own sentence, rather than paraphrased.
//
// ONE TEMPLATE MACHINERY FOR BOTH GLOSSARIES. There are two code glossaries in this console: the runtime and capability
// codes below, and the controller's run-failure codes, which live per stage in `./stages` and are read through
// `failure.ts`. They had two near-identical label functions and no import between them, which is how the two drifted.
// The machinery is in `./stages/stage.ts` (a leaf, so the stage files and this file never import each other) and is
// re-exported here, so a code is written for a reader in exactly one way whichever glossary it came out of.
//
// THE STATE SENTENCES live in the stage files too, one file per stage of the demo, and the table below is built from
// them. A state no stage file names has no sentence and is printed as itself.

import { fillTemplate, labelWithCode } from './stages/stage';
import type { CodeTemplate, TemplateArgs } from './stages/stage';
import { STAGE_STATES } from './stages/index';
import type { ViewState } from './types';

// The template machinery moved to ./stages/stage.ts (see the header there); every name is re-exported here so that
// the importers of this file are unchanged.
export { argsFromNamedValues, argText, fillTemplate, labelWithCode, NO_ARGS } from './stages/stage';
export type { CodeTemplate, TemplateArg, TemplateArgs } from './stages/stage';

export interface Reason {
  headline: string;
  detail: string | null;
  /** The records the sentence was built from, so a reader can go and check it. */
  basis: string[];
}

/**
 * The controller's real state names, from `bin/laptop/manipulation/run_single_cube_cycle.py`, each with its sentence. The
 * sentences live in the stage files (./stages), one per stage, and this is the one table built from them.
 */
const STATE_SENTENCE: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(STAGE_STATES).map(([state, entry]) => [state, entry.sentence]),
);

/** The state names this file has a sentence for, so a test can hold them against the ones the controller emits. */
export const STATES_WITH_A_SENTENCE: readonly string[] = Object.keys(STATE_SENTENCE);

/** The sentence for a state name, or null for one this console has never been told about. */
export function describeState(state: string): string | null {
  return STATE_SENTENCE[state] ?? null;
}

/**
 * Reason codes this console has a sentence for. An unknown code is shown as the code, never as a guess.
 *
 * These are the RUNTIME's and the capability list's codes, not the controller's: they ride on a refusal, a capability
 * or a snapshot, and what those records carry beside the code is a sentence, not a set of measurements. So every entry
 * here states no number, and `needs` is empty on all of them. The machinery is the same one the controller's failure
 * codes use, and a record that does start carrying values for one of these will state them through it.
 */
const REASON_SENTENCE: Record<string, CodeTemplate> = {
  owner_not_free: { gloss: 'something else holds the robot', needs: [] },
  owner_unresolved: { gloss: 'the runtime cannot tell who holds the robot', needs: [] },
  owner_external: { gloss: 'a controller this runtime did not start is running', needs: [] },
  replay_only: { gloss: 'this runtime serves recorded runs only and has no command path', needs: [] },
  contract_incompatible: { gloss: 'the runtime speaks a different contract version', needs: [] },
  not_built: { gloss: 'this does not exist yet', needs: [] },
  run_active: { gloss: 'a run is already in progress, and only stop is accepted while one is', needs: [] },
  no_active_run: { gloss: 'there is no run to stop', needs: [] },
  step_out_of_bounds: { gloss: 'the step was outside the controller bounds', needs: [] },
  start_pose_refused: { gloss: 'the start pose was refused', needs: [] },
  source_stamp_missing: { gloss: 'an observation arrived without its source sequence number', needs: [] },
  runtime_restarted_before_child_recorded: {
    gloss: 'the runtime died between accepting the command and recording the process',
    needs: [],
  },
  // Both spellings are live: the runtime writes the second one, and the first is what older records carry.
  hardware_commands_disabled: { gloss: 'this runtime was started without the hardware command path', needs: [] },
  hardware_commands_not_enabled: { gloss: 'this runtime was started without the hardware command path', needs: [] },
  link_unknown: { gloss: 'nothing has been received from the robot host yet', needs: [] },
};

/** The codes this glossary knows, so a test can pin them against the ones the runtime really writes. */
export const RUNTIME_REASON_CODES: readonly string[] = Object.keys(REASON_SENTENCE);

export function describeReasonCode(code: string | null): string | null {
  if (code === null) return null;
  return REASON_SENTENCE[code]?.gloss ?? null;
}

/** The sentence with the record's own values in it, where a record carried any. Null for a code that is not glossed. */
export function describeReasonCodeWith(code: string | null, args: TemplateArgs): string | null {
  if (code === null) return null;
  return fillTemplate(REASON_SENTENCE[code], args);
}

/** `code` rendered for a human: the sentence when there is one, otherwise the bare code. */
export function reasonCodeLabel(code: string | null): string | null {
  if (code === null) return null;
  return labelWithCode(describeReasonCode(code), code);
}

/** The same label, with any value the record carried stated inside the sentence. */
export function reasonCodeLabelWith(code: string | null, args: TemplateArgs): string | null {
  if (code === null) return null;
  return labelWithCode(describeReasonCodeWith(code, args), code);
}

function parameterText(state: ViewState): string | null {
  const action = state.current_action;
  if (action === null || action.parameters.length === 0) return null;
  return action.parameters
    .map((parameter) => `${parameter.name} ${String(parameter.value)}${parameter.unit === null ? '' : ' ' + parameter.unit}`)
    .join(', ');
}

/**
 * The sentence for the screen. Order of precedence: a refusal explains itself; otherwise the controller's state does,
 * with the action under way and its parameters; a failing predicate is added when there is one.
 */
export function buildReason(state: ViewState): Reason {
  const basis: string[] = [];
  const action = state.current_action;
  const machine = state.machine;

  if (action !== null && action.phase === 'refused' && action.refusal !== null) {
    basis.push(`action.refused at seq ${action.seq}`);
    const code = reasonCodeLabel(action.refusal.reason_code);
    return {
      headline: `The controller refused ${action.action_type}.`,
      detail: [action.refusal.detail, code === null ? null : `Reason: ${code}.`, action.refusal.before_motion ? 'Nothing had moved in this run when it refused.' : 'Something had already moved in this run when it refused.']
        .filter((piece): piece is string => piece !== null)
        .join(' '),
      basis,
    };
  }

  if (machine === null) {
    return {
      headline: state.active_run === null ? 'No run is in progress.' : 'The run has not reported a state yet.',
      detail: null,
      basis,
    };
  }

  basis.push(`state.transition to ${machine.state} at seq ${machine.since_seq}`);
  const headline = STATE_SENTENCE[machine.state] ?? `The controller is in state ${machine.state}.`;

  const pieces: string[] = [];
  if (action !== null && action.phase !== 'completed') {
    const parameters = parameterText(state);
    basis.push(`action.${action.phase} ${action.action_type} at seq ${action.seq}`);
    pieces.push(
      `Action ${action.action_type} is ${action.phase}${parameters === null ? '' : ` with ${parameters}`}${action.plan_only ? ', plan only: nothing is commanded' : ''}.`,
    );
  }

  const failing = state.predicates.filter((predicate) => predicate.value === false);
  const lastFailing = failing[failing.length - 1];
  if (lastFailing !== undefined) {
    basis.push(`predicate.evaluated ${lastFailing.predicate} at seq ${lastFailing.seq}`);
    const measured = lastFailing.measured === null ? 'unavailable' : String(lastFailing.measured);
    const threshold = lastFailing.threshold === null ? 'unavailable' : String(lastFailing.threshold);
    const unit = lastFailing.unit === null ? '' : ` ${lastFailing.unit}`;
    pieces.push(`Predicate ${lastFailing.predicate} is false: measured ${measured}${unit} against ${threshold}${unit}.`);
  }

  return { headline, detail: pieces.length === 0 ? null : pieces.join(' '), basis };
}
