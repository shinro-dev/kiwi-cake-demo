// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The one command whose execution is NOT known, and what the console may do while that stands.
//
// The rules here are safety rules and they are deliberately one-directional:
//
//  1. WHILE THE BLOCK STANDS, EVERY MOTION CONTROL IS OFF, with the reason `owner_unresolved`. The runtime sends the
//     list of commands it blocks; this console blocks its whole motion set whatever that list says. Blocking more than
//     the server does costs an operator one refused click; blocking less offers a button that sends a motion command
//     into a robot whose state nobody can describe.
//  2. THE AMBIGUOUS COMMAND CAN NEVER BE SENT AGAIN FROM THIS SCREEN. There is no control that takes a command_id and
//     submits it, and `canResend` exists so that a test can say so about any command this console is tracking.
//  3. A MALFORMED BLOCK IS STILL A BLOCK. If the runtime says there is an unresolved command and this console cannot
//     read the detail, the controls stay off and the screen says it cannot describe it. Reading it as "no block" would
//     turn a parse failure into permission to command.

import type { Attestation, UnresolvedCommand } from '../api/types';

/** The runtime's word for why a command is refused while one command's execution is unknown. */
export const BLOCK_REASON_CODE = 'owner_unresolved';

/**
 * Every control on this console that commands motion, by the capability id the controls panel uses. `stop` is NOT in
 * this list on purpose: asking a controller to stop is the one thing that must stay available when nobody knows what
 * is running.
 */
export const MOTION_CONTROLS: readonly string[] = [
  'start_cycle',
  'observe',
  'return_arm',
  'scan_scene',
  'manual_pan',
  'manual_move',
  'manual_grip',
  'manual_base_pulse',
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

function asStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item !== '');
}

function parseAttestation(value: unknown): Attestation | null {
  if (!isRecord(value)) return null;
  const finding = asText(value['finding']);
  if (finding === null) return null;
  return {
    finding,
    note: asText(value['note']),
    classification: asText(value['classification']),
    clears_block: value['clears_block'] === true,
    refused: asText(value['refused']),
    t_wall_utc: asText(value['t_wall_utc']),
  };
}

/**
 * Read the snapshot's additive `unresolved_command`. Returns null ONLY when the key is absent or is not an object at
 * all, which is the runtime saying there is nothing unresolved. A present object that is missing its command_id is
 * reported as a block this console cannot describe, never as no block: see rule 3 above.
 */
export function parseUnresolved(value: unknown): UnresolvedCommand | null {
  if (value === null || value === undefined) return null;
  if (!isRecord(value)) return null;
  const commandId = asText(value['command_id']);
  const evidence = isRecord(value['evidence']) ? value['evidence'] : null;
  return {
    command_id: commandId ?? '',
    run_id: asText(value['run_id']),
    command_type: asText(value['command_type']),
    reason_code: asText(value['reason_code']),
    classification: asText(value['classification']),
    block_reason_code: asText(value['block_reason_code']) ?? BLOCK_REASON_CODE,
    question: asText(value['question']),
    findings: asStrings(value['findings']),
    evidence,
    attestations: Array.isArray(value['attestations'])
      ? value['attestations'].map(parseAttestation).filter((item): item is Attestation => item !== null)
      : [],
    blocked_commands: asStrings(value['blocked_commands']),
  };
}

/** True when the console knows enough about the block to describe it on the screen. */
export function isDescribable(unresolved: UnresolvedCommand | null): boolean {
  return unresolved !== null && unresolved.command_id !== '';
}

/**
 * Why this control is off, or null when the unresolved command is not what is stopping it. It is `owner_unresolved`
 * for every motion control while any block stands, whatever the server's own list says: the server's list is shown to
 * the operator, and this console never takes it as permission for a control the list happens to leave out.
 */
export function blockedByUncertainty(unresolved: UnresolvedCommand | null, capabilityId: string): string | null {
  if (unresolved === null) return null;
  if (!MOTION_CONTROLS.includes(capabilityId)) return null;
  return unresolved.block_reason_code;
}

/**
 * Whether ANY command this console tracks may be sent a second time. It never may. This exists so the rule is a
 * function a test can call rather than the absence of a button somebody could add back.
 */
export function canResend(): false {
  return false;
}

/** The wording the operator's statement is offered under. It is an attestation and it says so. */
export const ATTESTATION_WORDING =
  'I am stating this; the console did not verify it. This is recorded as an attestation beside the evidence the runtime read for itself, and the runtime decides whether it clears the block.';

/**
 * What each finding means, in words. The findings themselves come from the runtime and this console offers no other:
 * a finding not in this table is offered under its own name, so a runtime that grows one is not silently dropped.
 */
const FINDING_SENTENCE: Record<string, string> = {
  no_controller_running: 'I looked at the robot and no controller is running.',
  controller_finished: 'I looked at the robot and the controller had finished on its own.',
  controller_stopped_by_operator: 'I looked at the robot and I stopped the controller myself.',
};

export function findingSentence(finding: string): string {
  return FINDING_SENTENCE[finding] ?? finding;
}

/** What each classification means, in words. An unknown classification is shown as itself. */
const CLASSIFICATION_SENTENCE: Record<string, string> = {
  ran_and_finished: "the controller's own record says this run ended, and no process of it is alive now",
  ran_and_was_interrupted: 'something started and left no terminal record, and no process of it is alive now',
  never_started: 'nothing on this machine shows that a controller ever started for this command',
  still_unknown: 'a check could not be read, so nothing is concluded and the block stays',
};

export function classificationSentence(classification: string | null): string | null {
  if (classification === null) return null;
  return CLASSIFICATION_SENTENCE[classification] ?? classification;
}

/** One readable line of the machine evidence: what was checked, whether it could be read, and what it said. */
export interface EvidenceLine {
  check: string;
  readable: boolean | null;
  summary: string;
}

/**
 * The evidence object, flattened for the screen. Only the keys the runtime actually sent become lines: an absent check
 * is absent, not a check that came back empty.
 */
export function evidenceLines(evidence: Record<string, unknown> | null): EvidenceLine[] {
  if (evidence === null) return [];
  const lines: EvidenceLine[] = [];
  const process = isRecord(evidence['process']) ? evidence['process'] : null;
  if (process !== null) {
    lines.push({
      check: 'process table',
      readable: typeof process['readable'] === 'boolean' ? process['readable'] : null,
      summary:
        process['readable'] === false
          ? 'could not be read, so a live controller cannot be ruled out'
          : process['alive'] === true
            ? `a process with this command's identity is alive now${asText(process['found_by']) === null ? '' : ` (${String(process['found_by'])})`}`
            : 'no process with this command identity is alive',
    });
  }
  const files = isRecord(evidence['run_files']) ? evidence['run_files'] : null;
  if (files !== null) {
    const records = typeof files['event_records'] === 'number' ? files['event_records'] : null;
    lines.push({
      check: "the run's own files",
      readable: typeof files['readable'] === 'boolean' ? files['readable'] : null,
      summary:
        files['readable'] === false
          ? 'could not be read'
          : files['terminal_record'] === true
            ? `a terminal record is present${asText(files['outcome']) === null ? '' : `, outcome ${String(files['outcome'])}`}`
            : `no terminal record${records === null ? '' : `, ${String(records)} record(s) written`}${files['output_present'] === true ? ', output file present' : ''}`,
    });
  }
  const observations = isRecord(evidence['observations']) ? evidence['observations'] : null;
  if (observations !== null && observations['checked'] === true) {
    const after = typeof observations['files_after_accept'] === 'number' ? observations['files_after_accept'] : null;
    lines.push({
      check: 'the observation store',
      readable: typeof observations['readable'] === 'boolean' ? observations['readable'] : null,
      summary:
        observations['readable'] === false
          ? 'could not be read'
          : after === null
            ? 'checked'
            : `${String(after)} file(s) written after the command was accepted`,
    });
  }
  const journal = isRecord(evidence['journal']) ? evidence['journal'] : null;
  if (journal !== null) {
    lines.push({
      check: "the runtime's own journal",
      readable: true,
      summary: `status ${asText(journal['status']) ?? 'unstated'}${
        asText(journal['reason_code']) === null ? '' : `, reason ${String(journal['reason_code'])}`
      }${journal['child_identity'] === true ? ', a child identity was recorded' : ', no child identity was recorded'}`,
    });
  }
  const unreadable = asStrings(evidence['unreadable']);
  if (unreadable.length > 0) {
    lines.push({
      check: 'what could not be read',
      readable: false,
      summary: unreadable.join(', '),
    });
  }
  return lines;
}
