// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHAT ONE STAGE FILE HOLDS, AND THE TEMPLATE MACHINERY ITS SENTENCES ARE WRITTEN WITH.
//
// The console's glossaries are split by the stage of the demo that decides them (docs/autonomous-demo/
// overnight-plan-2026-09-26.md, track P0b): search, align, grasp, placement and recovery, one file each, so that the
// track that owns a stage's policy edits that stage's sentences and nothing else. A stage file holds three things:
//
//   - the controller's FAILURE CODES the stage raises, each a template (the gloss, the values it needs, the measured
//     sentence);
//   - the STATES the stage emits, each with its sentence, the phase it is grouped in and the presentation step it is
//     drawn under;
//   - nothing else. The runtime's own codes stay in reason.ts, the next steps after a failure stay in failure.ts, and
//     the phase rules (which predicate decides a phase, which action label enters one) stay in phase.ts.
//
// THE TEMPLATE MACHINERY LIVES HERE, not in reason.ts, because a stage file needs `argText` and reason.ts needs the
// stage files: with the machinery in reason.ts the two would import each other, and a test that imported a stage file
// first would find reason.ts reading a stage that had not been evaluated yet. This file imports no model file, so
// every stage file can import it in any order. reason.ts re-exports every name below, so no other importer changes.
//
// A template states a number only when the record carried it. `needs` names the values the measured sentence states,
// every one of them is looked for on the record, and a name that is not there sends the sentence back to the wordy
// form rather than to a default. Nothing here rounds a recorded value either: what a sentence prints is what the
// controller wrote.

import type { NamedValue } from '@kiwi/demo-contracts';
import { UNAVAILABLE } from '../format';
import type { Phase, PresentationStep } from '../phase';

/** One value as the record carried it: the number or the word itself, and the unit the record named beside it. */
export interface TemplateArg {
  value: number | string | boolean | null;
  unit: string | null;
}

/** The values a sentence may state, by the name the record gave them. */
export type TemplateArgs = Readonly<Record<string, TemplateArg>>;

export interface CodeTemplate {
  /** The sentence with no measurement in it. It is what a record carrying none reads as, and it is never skipped. */
  gloss: string;
  /** The values `measured` states. Every one is checked against the record and not one of them is ever defaulted. */
  needs: readonly string[];
  /** The same sentence with the record's own values in it. Called only once every name in `needs` was found. */
  measured?: (args: TemplateArgs) => string;
}

/** The recorded values of a failure or a predicate, keyed by name, ready for a template to state. */
export function argsFromNamedValues(values: readonly NamedValue[]): TemplateArgs {
  const out: Record<string, TemplateArg> = {};
  for (const item of values) out[item.name] = { value: item.value, unit: item.unit };
  return out;
}

/** One value, written the way the record wrote it, with its unit when it named one. */
export function argText(args: TemplateArgs, name: string): string {
  const arg = args[name];
  if (arg === undefined || arg.value === null) return UNAVAILABLE;
  // A recorded float is printed to three decimals at most (the gripper readings are recorded to three), never with the
  // binary tail a float carries (67.3567977915804); integers and text print as recorded.
  const value =
    typeof arg.value === 'boolean'
      ? (arg.value ? 'true' : 'false')
      : typeof arg.value === 'number' && Number.isFinite(arg.value) && !Number.isInteger(arg.value)
        ? String(Math.round(arg.value * 1000) / 1000)
        : String(arg.value);
  return arg.unit === null ? value : `${value} ${arg.unit}`;
}

/**
 * The sentence for a code: the measured one when the record carried every value it states, the wordy one otherwise,
 * and null for a code no glossary knows.
 */
export function fillTemplate(template: CodeTemplate | undefined, args: TemplateArgs): string | null {
  if (template === undefined) return null;
  const write = template.measured;
  if (write === undefined) return template.gloss;
  for (const name of template.needs) {
    const arg = args[name];
    if (arg === undefined || arg.value === null) return template.gloss;
  }
  return write(args);
}

/** How a code is written for a reader on this screen: the sentence and the code, or the bare code when there is none. */
export function labelWithCode(sentence: string | null, code: string): string {
  return sentence === null ? code : `${sentence} (${code})`;
}

/** Nothing to substitute, for the many records that carry a sentence and no values. */
export const NO_ARGS: TemplateArgs = {};

/** The five stages of the demo, in the order a run passes through them. */
export type StageId = 'search' | 'align' | 'grasp' | 'placement' | 'recovery';

/** One state the controller emits: its sentence, the phase it is grouped in and the step of the five it is drawn under. */
export interface StageState {
  sentence: string;
  /** `terminal` is the end that is not a phase (DONE_ALREADY_PLACED). */
  phase: Phase | 'terminal';
  /** Null for a terminal state, which is drawn under no step. */
  step: PresentationStep | null;
}

export interface StageGlossary {
  stage: StageId;
  /** The controller's failure codes this stage raises. */
  failures: Readonly<Record<string, CodeTemplate>>;
  /** The controller's states this stage emits. */
  states: Readonly<Record<string, StageState>>;
}
