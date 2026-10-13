// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE VERDICT VOCABULARY (docs/autonomous-demo/console-redesign-2026-09-27.md, section 5.1; audit C3).
//
// Every verdict word the demo screen, the timeline and Diagnostics print comes from here, with one id (`data-verdict`)
// and one tone. The mission and its run are always both stated, each with its own word: "Mission failed" beside "Run 2
// of 2 stopped by the controller". A record token this console has no word for is drawn as itself, in the mute tone,
// never mapped onto the nearest known word.

import type { ContractEvent } from '../contracts/envelope';
import type { SignatureCheckRecord } from './signatureCheck';
import type { SlotReading } from './missionRecord';

/**
 * The tones of the theme (`design/theme.ts`): ok, bad, warn, wait, accent and mute, and `ink`, the design's brightest text
 * grey (`INK.bright`), which is neutral and loud: a fact that is neither good nor bad news and must not be missed (a Cake
 * refusal with no expectation served, verdict 2026-09-27 item 12). No verdict has another.
 */
export type Tone = 'ok' | 'bad' | 'warn' | 'wait' | 'accent' | 'mute' | 'ink';

export interface Verdict {
  id: string;
  word: string;
  tone: Tone;
}

const unknown = (token: string): Verdict => ({ id: 'unknown', word: token, tone: 'mute' });

/**
 * The run's reason codes that ask for the base capability at SEARCH, before anything is picked up, and hand the target back
 * to the mission, which holds for the capability and comes back to it: a cube that needs the base, and (under the additive
 * placement words) a cube the arm alone places whose fallback, should its release be refused and the loaded arm come back to
 * the carry pose, moves the base.
 */
export const CAPABILITY_HOLD_CODES: ReadonlySet<string> = new Set(['mission_capability_required', 'mission_fallback_capability_required']);

/**
 * The mission's own word, from its state, its waiting reason and whether its record carries a stop request. A STOPPED
 * mission is "Stopped on request" only when it does.
 */
export function missionVerdict(mission: { state: string; waiting_reason: string | null; stop_requested?: boolean }): Verdict {
  switch (mission.state) {
    case 'RUNNING':
      return mission.stop_requested === true ? { id: 'stopping', word: 'Stopping', tone: 'warn' } : { id: 'running', word: 'Running', tone: 'accent' };
    case 'WAITING_FOR_CAPABILITY':
      if (mission.waiting_reason === 'capability_unavailable') return { id: 'waiting_cake', word: 'Waiting for Cake', tone: 'wait' };
      if (mission.waiting_reason === 'operator_decision') return { id: 'held_for_you', word: 'Held for you', tone: 'warn' };
      return { id: 'held_other', word: `Held: ${mission.waiting_reason ?? 'no reason recorded'}`, tone: 'warn' };
    case 'COMPLETE':
      return { id: 'complete', word: 'Complete', tone: 'ok' };
    case 'STOPPED':
      // ON REQUEST ONLY WITH THE REQUEST ON THE RECORD (verify-r4 must_fix 1). The runtime ends the mission it carries at
      // its own shutdown and writes no stop request for it (`demo_runtime/mission_coordinator.py` close: nobody asked
      // for one); the record then reads STOPPED with `stop_requested` false. That word claims no cause: it says what
      // the record does not hold, which is as true of a record that predates the field as of one that says false.
      return mission.stop_requested === true
        ? { id: 'stopped_on_request', word: 'Stopped on request', tone: 'warn' }
        : { id: 'stopped_no_request', word: 'Stopped with no stop request on its record', tone: 'warn' };
    case 'FAILED':
      return { id: 'failed', word: 'Failed', tone: 'bad' };
    default:
      return unknown(mission.state);
  }
}

/**
 * THE CARD'S HEADLINE WHILE THE MISSION RECORD SAYS RUNNING AND ITS RUN HAS ENDED (redesign r4 verify-r1 must_fix 6), in
 * this display's voice: the run's end is on its own records, and the mission record has not yet said what the mission
 * did at it (the next attempt, a wait, an end). Drawn as it is, never as "Mission ...".
 */
export const RUN_ENDED_READING: Verdict = { id: 'run_ended_reading', word: 'Run ended; reading the mission record', tone: 'mute' };

/** Whether a mission word says the mission has ended. */
export function missionEnded(state: string | null | undefined): boolean {
  return state === 'COMPLETE' || state === 'FAILED' || state === 'STOPPED';
}

export interface RunVerdictInput {
  /** The run's `outcome` as the runtime reported it. */
  outcome: string | null;
  reason_code: string | null;
  /** Whether the run's own events hold a `stop.requested`. */
  stop_requested: boolean;
  /** Whether the run's command places a block (a cycle); a scan, Observe or Return arm does not. */
  placement: boolean;
  /** For a command that places nothing: whether it ended well (`nonPlacementCommandSucceeded`). */
  non_placement_done: boolean;
}

/** The run's word, from its outcome, its reason code and whether a stop was asked for in it. */
export function runVerdict(run: RunVerdictInput): Verdict {
  switch (run.outcome) {
    case 'placed_new':
      return { id: 'placed', word: 'Placed', tone: 'ok' };
    case 'already_placed_noop':
      return { id: 'already_placed', word: 'Already placed', tone: 'ok' };
    case 'stopped':
      return run.stop_requested
        ? { id: 'stopped_on_request', word: 'Stopped on request', tone: 'warn' }
        : { id: 'stopped_by_controller', word: 'Stopped by the controller', tone: 'bad' };
    case 'refused':
      return { id: 'refused', word: 'Refused', tone: 'bad' };
    case 'failed':
      return CAPABILITY_HOLD_CODES.has(run.reason_code ?? '')
        ? { id: 'needs_capability', word: 'Needs capability', tone: 'wait' }
        : { id: 'failed', word: 'Failed', tone: 'bad' };
    case 'unknown':
      return !run.placement && run.non_placement_done ? { id: 'done', word: 'Done', tone: 'mute' } : { id: 'no_outcome', word: 'No stated outcome', tone: 'warn' };
    case null:
      return { id: 'no_outcome', word: 'No stated outcome', tone: 'warn' };
    default:
      return unknown(run.outcome);
  }
}

function payloadOf(event: ContractEvent): Record<string, unknown> {
  return ((event as { payload?: unknown }).payload ?? {}) as Record<string, unknown>;
}

/**
 * THE RUN'S WORD FROM ITS OWN EVENTS: its newest `run.finished`, whether a `stop.requested` is among them, and the command
 * of its `run.started`. Null while they hold no `run.finished`: the finished-run list alone does not say whether a stop
 * was asked for, so no word is taken from it (verifier V13). The mission card, the timeline and History read this one
 * function, so they cannot give one run two words.
 */
export function runVerdictOf(events: readonly ContractEvent[]): Verdict | null {
  const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
  if (finished === undefined) return null;
  const payload = payloadOf(finished);
  const started = events.find((event) => event.kind === 'run.started');
  return runVerdict({
    outcome: typeof payload['outcome'] === 'string' ? payload['outcome'] : null,
    reason_code: typeof payload['reason_code'] === 'string' ? payload['reason_code'] : finished.reason_code,
    stop_requested: events.some((event) => event.kind === 'stop.requested'),
    placement: (started === undefined ? 'start_cycle' : payloadOf(started)['command_type']) === 'start_cycle',
    non_placement_done: payload['outcome'] === 'unknown' && (payload['exit_status'] === 0 || payload['exit_status'] === undefined),
  });
}

/**
 * THE RUN'S WORD FOR DIAGNOSTICS (spec 5.1; browser r2 N2, verifier R-4): from its own kept events where they are read,
 * else from a fold that saw the run begin (its stop request is then in the fold). Null for a stop whose request nobody
 * read, because its word depends on it: it is never guessed from the outcome token.
 */
export function runWordFor(input: {
  events: readonly ContractEvent[] | undefined;
  view: { run_started: { run_id?: string | null } | null; stop: { requested: unknown } };
  run_id: string | null;
  outcome: string | null;
  reason_code: string | null;
  placement: boolean;
}): Verdict | null {
  const fromEvents = input.events === undefined ? null : runVerdictOf(input.events);
  if (fromEvents !== null) return fromEvents;
  const saw = input.run_id !== null && (input.view.run_started?.run_id ?? null) === input.run_id;
  if (input.outcome === 'stopped' && !saw) return null;
  return runVerdict({ outcome: input.outcome, reason_code: input.reason_code, stop_requested: saw && input.view.stop.requested !== null, placement: input.placement, non_placement_done: false });
}

/** The run words a stage row carries where the run ended, with "here": "Stopped here by the controller". */
export const RUN_WORD_HERE: Record<string, string> = {
  stopped_by_controller: 'Stopped here by the controller',
  stopped_on_request: 'Stopped here on request',
  failed: 'Failed here',
  refused: 'Refused here',
  no_outcome: 'Ended here with no stated outcome',
};

/**
 * Whether the slot moved between the two reads beside an activation. `unchanged` only when both reads answered (`read`
 * ok) and generation, capsule and instance are equal; `changed` when both answered and one differs; `unknown` otherwise.
 */
export function slotChange(before: SlotReading | null | undefined, after: SlotReading | null | undefined): 'unchanged' | 'changed' | 'unknown' {
  if (before === null || before === undefined || after === null || after === undefined) return 'unknown';
  if (before.read !== 'ok' || after.read !== 'ok') return 'unknown';
  const same = before.generation === after.generation && before.capsule === after.capsule && before.instance === after.instance;
  return same ? 'unchanged' : 'changed';
}

/** The two states in which the resident refused an activation. */
export const REFUSALS: ReadonlySet<string> = new Set(['REJECTED_BY_CAKE', 'REFUSED_BEFORE_TRANSACTION']);

/**
 * An activation's word, from the state the runtime recorded, the slot reads beside it and the state its candidate was
 * expected to record (`expect`, served only when the runtime's configuration names one). Without an expectation a
 * refusal is neither good nor bad news, and it is the Cake moment of the demo: it reads "Cake refused the successor" in
 * the neutral, loud `ink` tone, never as a warning and never muted (verdict 2026-09-27 item 12). Red when the served
 * expectation says otherwise or the slot changed.
 *
 * GREEN ONLY WITH A RECORD AND A SERVED EXPECTATION IT MET (should_fix honesty S4): a refusal "as expected" needs both slot
 * reads to show the slot unchanged, the before-any-transaction refusal as much as Cake's own; with a read missing it is
 * the neutral refusal. An activation the runtime observed with no expectation served is "Activated" in the neutral
 * bright ink, as loud as a refusal and never green: nothing said to expect it.
 */
export function activationVerdict(
  activation: { activation_state: string; slot_before?: SlotReading | null; slot_after?: SlotReading | null },
  expectState: string | null | undefined,
): Verdict {
  const state = activation.activation_state;
  const expected = expectState ?? null;
  const change = slotChange(activation.slot_before, activation.slot_after);
  if (REFUSALS.has(state)) {
    if (change === 'changed') return { id: 'refused_slot_changed', word: 'Refused, but the slot changed', tone: 'bad' };
    if (expected !== null && expected !== state) return { id: 'refused_unexpected', word: 'Refused, not as expected', tone: 'bad' };
    if (state === 'REJECTED_BY_CAKE') {
      return expected === state && change === 'unchanged'
        ? { id: 'refused_as_expected', word: 'Refused as expected', tone: 'ok' }
        : { id: 'refused_by_cake', word: 'Cake refused the successor', tone: 'ink' };
    }
    return expected === state && change === 'unchanged'
      ? { id: 'refused_before_as_expected', word: 'Refused before any transaction, as expected', tone: 'ok' }
      : { id: 'refused_before', word: 'Cake refused the successor before any transaction', tone: 'ink' };
  }
  switch (state) {
    case 'OBSERVED':
      if (expected === null) return { id: 'activated', word: 'Activated', tone: 'ink' };
      return expected === 'OBSERVED'
        ? { id: 'activated', word: 'Activated', tone: 'ok' }
        : { id: 'activated_unexpected', word: 'Activated, not as expected', tone: 'bad' };
    case 'COMPLETED_NOT_OBSERVED':
      return expected === state
        ? { id: 'inert_as_expected', word: 'Back to inert, as expected', tone: 'ok' }
        : { id: 'completed_inactive', word: 'Completed, capability not active', tone: 'warn' };
    case 'REQUEST_FAILED':
      return { id: 'not_delivered', word: 'Did not reach Cake', tone: 'warn' };
    case 'SUBMITTED':
      return { id: 'pending', word: "Waiting for Cake's answer", tone: 'wait' };
    default:
      return unknown(state);
  }
}

/** The signature check's word, against the runtime session the snapshot names now. Null: no check served. */
export function signatureVerdict(check: SignatureCheckRecord | null, currentSession: string | null): Verdict {
  if (check === null) return { id: 'sig_none', word: 'No signature check in this session', tone: 'mute' };
  if (check.runtime_session === null || check.runtime_session !== currentSession) return { id: 'sig_earlier', word: 'Earlier session', tone: 'mute' };
  if (check.rows_not_as_expected > 0 || check.rows_error > 0) return { id: 'sig_bad', word: 'Not as expected', tone: 'bad' };
  if (check.rows_skipped > 0) return { id: 'sig_partial', word: `As expected, ${String(check.rows_skipped)} skipped`, tone: 'warn' };
  return { id: 'sig_ok', word: 'Signatures as expected', tone: 'ok' };
}

export type ReadinessId = 'ready' | 'not_ready' | 'steps_first' | 'running' | 'waiting_cake' | 'held_for_you';

const READINESS: Record<ReadinessId, Verdict> = {
  ready: { id: 'ready', word: 'Ready', tone: 'ok' },
  not_ready: { id: 'not_ready', word: 'Not ready', tone: 'bad' },
  steps_first: { id: 'steps_first', word: 'Steps first', tone: 'warn' },
  running: { id: 'running', word: 'Running', tone: 'accent' },
  waiting_cake: { id: 'waiting_cake', word: 'Waiting for Cake', tone: 'wait' },
  held_for_you: { id: 'held_for_you', word: 'Held for you', tone: 'warn' },
};

/** The readiness verdict's word (rule 4.2). */
export function readinessVerdict(id: ReadinessId): Verdict {
  return READINESS[id];
}
