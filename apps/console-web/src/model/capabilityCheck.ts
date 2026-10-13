// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAPABILITY SLOT CHECK, as the runtime serves it (GET /api/capability/check), read at the boundary.
//
// WHAT IT IS. One prepared Plan applied to the robot's capability slot and read back, with NO motion and never for a
// mission: the runtime refuses it unless a fresh read of the robot's resident reports the pinned build, exactly the
// session this console sends and the supervised host HELD, meaning NO host process under the supervisor slot
// (`demo_runtime/capability_check.py`, host_held). It exists so the non-motion activation controls can be driven against
// the robot's resident while no mission is waiting.
//
// WHAT THIS FILE DOES. It reads the runtime's answer field by field, the way `missionRecord.ts` reads the mission, and it
// decides the two things a screen needs and nothing more: whether the Apply button is off, and why, and which rows of a
// recorded check must be drawn red (a host that was not held throughout, a resident session that changed). The verdict
// of a check is the runtime's, decided by the same lines that decide a mission's activation and narrowed only DOWN by the
// runtime (a changed session, a slot that did not change); nothing here upgrades one.
//
// HELD AND CHANGED ARE THE RUNTIME'S WORDS, NEVER DERIVED HERE. `held` (per host reading) and `slot_changed` (per check)
// are decided by the runtime's one rule each and drawn as they come. A host whose `host_running` is false is NOT drawn as
// held on that alone: the runtime answers false for a host process the supervisor reports in a state other than HEALTHY,
// and that host is not held. A missing `held` is drawn as not read, and a missing `slot_changed` as not known.
// `host_held_throughout` is the runtime's too: two held reads are held THROUGHOUT only when the supervisor's counters
// (spawns, restarts, slot generation and instance) read the same at both, and the runtime's `host_throughout_detail`
// names what differed. An ApplyPlan whose answer did not arrive in time (`unresolved`) is never said to have not landed:
// it may still be running.
//
// THE SESSION THIS CONSOLE SENDS IS THE ONE THE CURRENT READING SHOWS. It is shown on the card beside the button, so the
// operator presses for a resident session they can see. A reading too old to stand for the robot now turns the button
// off rather than sending a session read a while ago.

import type { MissionActivationRecord, MissionCandidate, SlotReading } from './missionRecord';
import { parseActivation, parseCandidates, parseSlotReading } from './missionRecord';
import { ROBOT_ANSWER_STALE_AFTER_MS } from './robot';
import { utcStamp } from './utcTime';
import { activationVerdict } from './verdict';

/** The only version of this surface this console speaks. It moves on its own, apart from every other version. */
export const CAPABILITY_CHECK_VERSION = '1';

/** What one read of the resident said about itself and its supervised host, before or after a check. */
export interface CheckHost {
  session_uuid: string | null;
  build_identity: string | null;
  /** Whether a HEALTHY host runs under the supervisor slot, as the robot link answers it. False alone is NOT held. */
  host_running: boolean | null;
  /** The runtime's rule: true is HELD (no host process under the supervisor slot), false is not, null is not read. */
  held: boolean | null;
  reason_code: string | null;
  observed_wall_utc: string | null;
}

/**
 * What each prepared candidate is FOR, as the runtime's configuration names it (`cake_activation.ROLE_EXPECT`), and the
 * activation state its apply is expected to record. A role is a label and an expectation, never a permission: a press
 * still sends the key and the digest and nothing else. A runtime that names no roles sends key and digest only, and each
 * candidate then reads role null, labelled by its key.
 */
export type CandidateRole = 'activate' | 'refused-demo' | 'tampered-demo' | 'undo';
const ROLES: readonly CandidateRole[] = ['activate', 'refused-demo', 'tampered-demo', 'undo'];

export interface CandidateRoleView {
  key: string;
  payload_digest: string | null;
  role: CandidateRole | null;
  label: string;
  /** The activation state an apply of this candidate is expected to record, as the runtime spells it. */
  expect: string | null;
}

/** The resident's refusal class when it refused the candidate before any transaction (`cake_activation.refusal_label`). */
export interface CheckRefusal {
  code: string | null;
  token: string | null;
  /** integrity, request_shape, plan, resident, rollback or unclassified. */
  kind: string;
  words: string | null;
}

/** ValidatePlan's verdict on the exact bytes, asked just before the submit. It is evidence and never gates the apply. */
export interface CheckValidation {
  /** valid, invalid, unanswered (asked, no verdict) or not_asked (no validator configured). */
  verdict: string;
  plan_reason_code: string | null;
  plan_artifact_digest: string | null;
  reason_code: string | null;
}

/** The states the transaction passed through, rebuilt by the runtime from the resident's flight ring after the answer. */
export interface CheckStatePath {
  /** ok, failed, no_transaction, not_in_ring or not_asked. */
  read: string;
  states: string[];
  terminal: string | null;
  /** False when the ring no longer holds the transaction's first transition. Null when not read. */
  starts_at_created: boolean | null;
  refused_transitions: number | null;
}

/** One recorded check: the fields of a mission activation, so the same evidence component draws it, and its own. */
export interface CapabilityCheckRecord extends MissionActivationRecord {
  requested_session_uuid: string | null;
  host_before: CheckHost | null;
  host_after: CheckHost | null;
  /**
   * True only when the reads before AND after were both held AND the supervisor's counters read the same at both. False
   * when either was not held or a counter differed. Null when it cannot be told (the read after did not answer, or a
   * read carries no counters).
   */
  host_held_throughout: boolean | null;
  /** The runtime's sentence for a host not held throughout (naming what differed) or not known to be. Null when held. */
  host_throughout_detail: string | null;
  /** The runtime says this operation's fate is still open (still running, or no answer in time); the next check waits. */
  unresolved: boolean;
  resident_session_changed: boolean | null;
  /** The runtime's answer to whether the slot CHANGED (capsule, generation or instance). Null: not known. */
  slot_changed: boolean | null;
  answered: boolean;
  /** An attempt a runtime that has since ended wrote and never answered. */
  orphaned: boolean;
  duration_ms: number | null;
  answer_wall_utc: string | null;
  /** Present when the resident refused before any transaction. Null otherwise, and for a record that predates it. */
  refusal: CheckRefusal | null;
  validation: CheckValidation | null;
  state_path: CheckStatePath | null;
}

/** The robot's resident as the runtime's cached reading reports it, which is what an apply would be sent against. */
export interface CapabilityCheckCurrent {
  session_uuid: string | null;
  build_identity: string | null;
  host_running: boolean | null;
  /** The runtime's rule for HELD, applied to this reading. Null: not read, or a runtime that does not say. */
  host_held: boolean | null;
  host_reason_code: string | null;
  slot: SlotReading | null;
  observed_wall_utc: string | null;
  /** How old the runtime's reading was when it answered, on the runtime's clock. */
  age_ms: number | null;
}

export interface CapabilityCheckState {
  capability_check_version: string;
  in_flight: boolean;
  pending_operation_uuid: string | null;
  /** The first reason the runtime would refuse an apply now, judged from its cached reading. Null: none it can see. */
  off_reason: string | null;
  candidates: MissionCandidate[];
  /** The same candidates with their configured role, label and expected outcome, in the runtime's order. */
  roles: CandidateRoleView[];
  current: CapabilityCheckCurrent;
  /** Newest last, as the runtime bounds them. */
  checks: CapabilityCheckRecord[];
  checks_dropped: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function asCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function parseHost(value: unknown): CheckHost | null {
  if (!isRecord(value)) return null;
  return {
    session_uuid: asText(value['session_uuid']),
    build_identity: asText(value['build_identity']),
    host_running: asBool(value['host_running']),
    held: asBool(value['held']),
    reason_code: asText(value['reason_code']),
    observed_wall_utc: asText(value['observed_wall_utc']),
  };
}

function parseRefusal(value: unknown): CheckRefusal | null {
  if (!isRecord(value)) return null;
  return { code: asText(value['code']), token: asText(value['token']), kind: asText(value['kind']) ?? 'unclassified', words: asText(value['words']) };
}

function parseValidation(value: unknown): CheckValidation | null {
  if (!isRecord(value)) return null;
  const verdict = asText(value['verdict']);
  if (verdict === null) return null;
  return {
    verdict,
    plan_reason_code: asText(value['plan_reason_code']),
    plan_artifact_digest: asText(value['plan_artifact_digest']),
    reason_code: asText(value['reason_code']),
  };
}

function parseStatePath(value: unknown): CheckStatePath | null {
  if (!isRecord(value)) return null;
  const read = asText(value['read']);
  if (read === null) return null;
  const states = Array.isArray(value['states']) ? value['states'].filter((item): item is string => typeof item === 'string') : [];
  return {
    read,
    states,
    terminal: asText(value['terminal']),
    starts_at_created: asBool(value['starts_at_created']),
    refused_transitions: asCount(value['refused_transitions']),
  };
}

/** The candidates with their roles, read from the runtime's own list. A row without a key is not offered. */
export function parseCandidateRoles(value: unknown): CandidateRoleView[] {
  const rows = isRecord(value) && Array.isArray(value['candidates']) ? value['candidates'] : [];
  const out: CandidateRoleView[] = [];
  for (const row of rows) {
    if (!isRecord(row)) continue;
    const key = asText(row['key']);
    if (key === null) continue;
    const role = ROLES.find((item) => item === row['role']) ?? null;
    out.push({
      key,
      payload_digest: asText(row['payload_digest']),
      role,
      label: role === null ? key : (asText(row['label']) ?? key),
      expect: role === null ? null : asText(row['expect']),
    });
  }
  return out;
}

/** One recorded check, or null when it carries no operation id or state (there is then nothing to report about it). */
export function parseCheck(value: unknown): CapabilityCheckRecord | null {
  const activation = parseActivation(value);
  if (activation === null || !isRecord(value)) return null;
  return {
    ...activation,
    requested_session_uuid: asText(value['requested_session_uuid']),
    host_before: parseHost(value['host_before']),
    host_after: parseHost(value['host_after']),
    host_held_throughout: asBool(value['host_held_throughout']),
    host_throughout_detail: asText(value['host_throughout_detail']),
    unresolved: value['unresolved'] === true,
    resident_session_changed: asBool(value['resident_session_changed']),
    slot_changed: asBool(value['slot_changed']),
    answered: value['answered'] === true,
    orphaned: value['orphaned'] === true,
    duration_ms: asCount(value['duration_ms']),
    answer_wall_utc: asText(value['answer_wall_utc']),
    refusal: parseRefusal(value['refusal']),
    validation: parseValidation(value['validation']),
    state_path: parseStatePath(value['state_path']),
  };
}

/**
 * The runtime's answer, or null when it is not one this console speaks. A version this console does not know is not
 * read at all: a check surface that changed meaning must not be drawn with this one's words.
 */
export function parseCapabilityCheckState(value: unknown): CapabilityCheckState | null {
  if (!isRecord(value) || value['ok'] !== true) return null;
  if (value['capability_check_version'] !== CAPABILITY_CHECK_VERSION) return null;
  const current = isRecord(value['current']) ? value['current'] : {};
  const checks: CapabilityCheckRecord[] = [];
  for (const item of Array.isArray(value['checks']) ? value['checks'] : []) {
    const check = parseCheck(item);
    if (check !== null) checks.push(check);
  }
  return {
    capability_check_version: CAPABILITY_CHECK_VERSION,
    in_flight: value['in_flight'] === true,
    pending_operation_uuid: asText(value['pending_operation_uuid']),
    off_reason: asText(value['off_reason']),
    candidates: parseCandidates(value),
    roles: parseCandidateRoles(value),
    current: {
      session_uuid: asText(current['session_uuid']),
      build_identity: asText(current['build_identity']),
      host_running: asBool(current['host_running']),
      host_held: asBool(current['host_held']),
      host_reason_code: asText(current['host_reason_code']),
      slot: parseSlotReading(current['slot']),
      observed_wall_utc: asText(current['observed_wall_utc']),
      age_ms: asCount(current['age_ms']),
    },
    checks,
    checks_dropped: asCount(value['checks_dropped']) ?? 0,
  };
}

/** Why the runtime would refuse an apply, in words, by its code. A code with no words here is printed as itself. */
export const CHECK_OFF_WORDS: Record<string, string> = {
  capability_check_in_flight: 'a check is already in flight, or the last one may not have finished (the resident said it is still running, or its answer did not arrive in time); nothing is sent twice',
  mission_running: 'a mission is being carried, and a check never runs under one',
  run_active: 'a controller run is active, and a check never runs under one',
  owner_unresolved: 'a command is unresolved, and nothing but stop is accepted until it is settled',
  robot_host_not_held:
    'a host process is under the supervisor slot (running, or reported but not HEALTHY); this check keeps the host held, so it is refused while one is there',
  robot_host_unreadable: 'the supervised host cannot be read, so nothing says it is held',
  robot_unreachable: 'the robot\'s resident cannot be read now',
  robot_resident_not_the_robot: 'the resident answering reports a build other than the one pinned for the robot',
  robot_build_identity_unreadable: 'the resident answering reports no readable build identity, so the build pin cannot be checked',
  robot_session_mismatch: 'the robot\'s resident reports another session than the one shown here',
  capability_slot_session_mismatch: 'the capability slot was read from another resident session than the one shown here',
};

export function checkOffWords(code: string): string {
  return `${CHECK_OFF_WORDS[code] ?? 'the runtime would refuse it'} (${code})`;
}

/**
 * WHY THE APPLY BUTTON IS OFF, or null when it may be pressed. In this order: the tab may not command at all; a press is
 * already in flight from this tab; the runtime says a check is in flight or would refuse one; there is nothing to pick;
 * the reading names no session; the reading is too old to stand for the robot now; nothing is picked (nothing ever is
 * until the operator picks); the pick carries no digest.
 *
 * `readAtMs` is when this tab received the state; the runtime's own `age_ms` is added to it, so a reading that was
 * already old when it was served is not made young by being fetched again.
 */
export function checkApplyOff(input: {
  state: CapabilityCheckState;
  readAtMs: number | null;
  nowMs: number;
  applying: boolean;
  chosen: string | null;
  readOnlyReason: string | null;
  /**
   * Given, the too-old reading is dated by the time the reading was observed, against this UTC day, and never by an age
   * (spec 5.4, 6.8: Diagnostics counts nothing against this display's clock).
   */
  day?: string | null;
}): string | null {
  const { state, readAtMs, nowMs, applying, chosen, readOnlyReason } = input;
  if (readOnlyReason !== null) return readOnlyReason;
  if (applying) return 'a check from this tab is in flight; nothing is sent twice';
  if (state.in_flight || state.pending_operation_uuid !== null) return checkOffWords('capability_check_in_flight');
  if (state.off_reason !== null) return checkOffWords(state.off_reason);
  if (state.candidates.length === 0) return 'the runtime has offered no prepared Plan';
  // THE READING BEFORE THE CHOICE: nothing is preselected, so "choose one" is the usual state of the card, and a reading
  // that could not be sent anyway is the more important thing to say.
  if (state.current.session_uuid === null) return 'the current reading names no resident session to send';
  const age = checkReadingAgeMs(state, readAtMs, nowMs);
  if (age === null || age > ROBOT_ANSWER_STALE_AFTER_MS) {
    if (input.day !== undefined) {
      const observed = utcStamp(state.current.observed_wall_utc, input.day);
      return `the reading of the robot ${observed === null ? 'carries no time' : `was observed at ${observed.text}`} and is too old to send its session`;
    }
    return `the reading of the robot is ${age === null ? 'of unknown age' : `${String(Math.round(age / 1000))} s old`}, too old to send its session`;
  }
  const candidate = chosen === null ? undefined : state.candidates.find((item) => item.key === chosen);
  if (candidate === undefined) return 'choose one of the prepared Plans first';
  if (candidate.payload_digest === null) return 'the runtime offered no digest for that Plan, so there is nothing to bind the request to';
  return null;
}

/** How old the reading behind this state is now: the runtime's age when it answered plus the time since it arrived. */
export function checkReadingAgeMs(state: CapabilityCheckState, readAtMs: number | null, nowMs: number): number | null {
  if (readAtMs === null || state.current.age_ms === null) return null;
  return state.current.age_ms + Math.max(0, nowMs - readAtMs);
}

/** A row the screen draws RED under a recorded check, because the check did not keep what it promised. */
export interface CheckWarning {
  key: 'host_not_held' | 'session_changed';
  text: string;
}

/**
 * One host reading in words, by the runtime's `held`. A reading the runtime did not call held is never drawn as held,
 * whatever `host_running` says: false there also covers a host process reported in a state other than HEALTHY.
 */
export function checkHostWords(host: { host_running: boolean | null; held: boolean | null; reason_code: string | null } | null): string {
  if (host === null) return 'not read';
  if (host.held === true) return 'held (no host process under the supervisor slot)';
  if (host.host_running === true) return 'RUNNING under the supervisor slot';
  if (host.held === false) {
    return host.reason_code === 'supervisor_host_not_healthy'
      ? 'NOT held (a host process is under the supervisor slot, reported but not HEALTHY)'
      : `NOT held (the runtime does not count ${host.reason_code ?? 'this reading'} as held)`;
  }
  return 'not read';
}

/**
 * Whether the host was held for the whole check, in words, by the runtime's `host_held_throughout` and its sentence.
 * Two held reads alone are never drawn as held throughout: the runtime says so only when the supervisor's counters agree.
 */
export function checkThroughoutWords(check: CapabilityCheckRecord): string {
  const detail = check.host_throughout_detail === null ? '' : `: ${check.host_throughout_detail.replace(/\.$/, '')}`;
  if (check.host_held_throughout === true) {
    return 'host held throughout (both reads held, and the supervisor\'s spawns, restarts, slot generation and instance read the same at both)';
  }
  if (check.host_held_throughout === false) return `host NOT held throughout${detail}`;
  return `whether the host was held throughout is not known${detail}`;
}

export function checkWarnings(check: CapabilityCheckRecord): CheckWarning[] {
  const out: CheckWarning[] = [];
  if (check.host_held_throughout === false) {
    const after = check.host_after?.held === false;
    const detail = check.host_throughout_detail;
    out.push({
      key: 'host_not_held',
      text: detail !== null
        ? `The host was NOT held throughout: ${detail.replace(/\.$/, '')}.`
        : after
          ? 'The host was NOT held throughout: a host process was under the supervisor slot when it was read after the operation.'
          : 'The host was NOT held throughout: the reads before and after do not both say that no host process was there.',
    });
  }
  if (check.resident_session_changed === true) {
    out.push({
      key: 'session_changed',
      text: 'The resident session CHANGED across the operation: the slot read afterwards is another resident session\'s, so it is not an observation of this check.',
    });
  }
  return out;
}

/**
 * What one recorded check came to, in the card's words. Never the word the mission uses for its capability.
 *
 * A CAKE REFUSAL IS WORDED AND COLOURED AS THE ACTIVATION CARD WORDS IT (spec 5.1, 5.5; `activationVerdict`): green only
 * with the expectation its candidate's role served and met, the neutral bright ink with none served, red only when the refusal was not
 * what the role expects or the slot changed under it. `roles` is the runtime's candidate list; a check whose candidate it
 * does not serve with an expectation has none.
 */
export function checkHeadline(check: CapabilityCheckRecord, roles: readonly CandidateRoleView[]): { words: string; tone: 'ok' | 'bad' | 'warn' | 'wait' | 'mute' | 'ink' } {
  switch (check.activation_state) {
    case 'OBSERVED':
      // The runtime says OBSERVED only when the slot CHANGED and, read back, carries the capsule its binding names.
      return { words: 'Observed: the slot changed and the read back carries the capability capsule', tone: 'ok' };
    case 'REJECTED_BY_CAKE': {
      const verdict = activationVerdict(check, roles.find((item) => item.key === check.candidate_key)?.expect ?? null);
      return { words: verdict.word, tone: verdict.tone === 'ok' || verdict.tone === 'bad' || verdict.tone === 'ink' ? verdict.tone : 'mute' };
    }
    case 'REFUSED_BEFORE_TRANSACTION': {
      // CAKE JUDGED THE CANDIDATE AND OPENED NO TRANSACTION. An integrity refusal is a Plan whose bytes do not hold their
      // digests; it is never a signature refusal, and it is never a request that did not land.
      const verdict = activationVerdict(check, roles.find((item) => item.key === check.candidate_key)?.expect ?? null);
      const tone = verdict.tone === 'ok' || verdict.tone === 'bad' || verdict.tone === 'ink' ? verdict.tone : 'mute';
      return check.refusal?.kind === 'integrity'
        ? { words: `${verdict.word}: an integrity check failed (the Plan bytes do not hold their digests)`, tone }
        : {
            words: `${verdict.word} (refusal class ${check.refusal?.code ?? 'not reported'}${
              check.refusal?.token === null || check.refusal?.token === undefined ? '' : `, ${check.refusal.token}`
            })`,
            tone,
          };
    }
    case 'REQUEST_FAILED':
      // AN ANSWER THAT DID NOT ARRIVE IN TIME IS NOT A REQUEST THAT DID NOT LAND: the operation may still be running, and
      // the runtime keeps the next check refused until its fate is read from the slot.
      if (check.unresolved) return { words: 'No answer in time; the operation may still be running', tone: 'wait' };
      return { words: 'The request did not land, so nothing was judged', tone: 'warn' };
    case 'COMPLETED_NOT_OBSERVED':
      // THREE DIFFERENT THINGS the runtime records under this one state, told apart here because they read opposite ways
      // on the slot line. A changed resident session and a slot that did not change are the runtime narrowing what would
      // otherwise have read OBSERVED; the slot read back then DOES carry the capsule, and saying it does not would be false.
      if (check.resident_session_changed === true) {
        return { words: 'Completed; the slot was read from another resident session, so nothing was observed', tone: 'warn' };
      }
      if (check.slot_after?.capability_active === true) {
        return check.slot_changed === false
          ? { words: 'Completed; the slot already carried the capability capsule and did not change, so no change was observed', tone: 'warn' }
          : { words: 'Completed; whether the slot changed is not known, so no change was observed', tone: 'warn' };
      }
      // NOT "nothing changed": a Plan that returns the slot to its inert capsule completes exactly like this, and the
      // slot line under it says whether the slot changed. What is not there is the capability capsule.
      return { words: 'Completed; the slot read back does not carry the capability capsule', tone: 'warn' };
    case 'SUBMITTED':
      if (check.orphaned) return { words: 'No answer was recorded', tone: 'warn' };
      if (check.answered) return { words: 'The resident says the transaction is still running', tone: 'wait' };
      return { words: 'In flight', tone: 'wait' };
    default:
      return { words: `The runtime recorded ${check.activation_state}`, tone: 'mute' };
  }
}

/**
 * Whether the slot changed across the check, in words. WHETHER is the runtime's `slot_changed`, decided from the two
 * readings it recorded (both answered, capsule, generation and instance all read); it is never worked out again here.
 * The two readings only supply the numbers the sentence prints. Anything but true or false is said to be unknown.
 */
export function slotChangeWords(check: CapabilityCheckRecord): string {
  const before = check.slot_before ?? null;
  const after = check.slot_after ?? null;
  if (check.slot_changed === null || before === null || after === null) {
    return 'slot: whether it changed is not known, because a reading before or after did not answer or lacks a component';
  }
  if (!check.slot_changed) {
    return `slot: unchanged (generation ${before.generation ?? 'not reported'}, same capsule and instance)`;
  }
  return `slot: changed (generation ${before.generation ?? 'not reported'} to ${after.generation ?? 'not reported'}; the capsule ${
    before.capsule === after.capsule ? 'is the same' : 'is different'
  }; the instance ${before.instance === after.instance ? 'is the same' : 'is new'})`;
}

/** Why a runtime offers no check, by the `detail` it sends beside `capability_check_unavailable`. */
export const CHECK_UNAVAILABLE_WORDS: Record<string, string> = {
  capability_check_requires_live_mode: 'this runtime is not LIVE, and a check is only ever sent to the robot\'s resident',
  robot_not_configured: 'this runtime names no robot resident',
  robot_build_identity_not_configured: 'this runtime pins no build identity for the robot\'s resident',
  cake_not_configured: 'this runtime names no admin client and socket to reach the resident with',
  no_activation_candidate_prepared: 'this runtime was configured with no prepared Plan to apply',
  capability_not_configured: 'this runtime names no capability slot',
  capability_binding_not_bindable: 'the configured capability slot and capsule are not in the shape a check can bind to',
};

/** One sentence for a runtime that offers no check: the runtime's detail in words, or its code as itself. */
export function checkUnavailableWords(reasonCode: string, detail: string | null): string {
  if (detail !== null) return `${CHECK_UNAVAILABLE_WORDS[detail] ?? 'the runtime says it cannot run one'} (${detail})`;
  return reasonCode === 'capability_check_unavailable' ? 'the runtime says it cannot run one' : reasonCode;
}

/**
 * The Plan a press would send: the one the operator picked, while it is still offered. NOTHING IS PRESELECTED: with no
 * choice, or a choice no longer offered, there is none, and the Apply button says to choose one. The runtime sorts its
 * keys, so a first-offered default was the abort-swap candidate, which is never what a press should send unasked.
 */
export function checkChoice(state: CapabilityCheckState, chosen: string | null): string | null {
  return chosen !== null && state.candidates.some((item) => item.key === chosen) ? chosen : null;
}

/** The key of the `activate` role, the panel's primary action, or null. Nothing is guessed from a key's spelling. */
export function primaryCandidate(state: CapabilityCheckState): string | null {
  return state.roles.find((item) => item.role === 'activate')?.key ?? null;
}

/** An expected activation state in words, for the label beside a candidate's button. */
export const EXPECT_WORDS: Record<string, string> = {
  OBSERVED: 'expected: the slot changes and, read back, carries the capability',
  REJECTED_BY_CAKE: 'expected: Cake opens the transaction and aborts it; the slot does not change',
  REFUSED_BEFORE_TRANSACTION: 'expected: refused by an integrity check before any transaction; the slot does not change',
  COMPLETED_NOT_OBSERVED: 'expected: the transaction completes and the slot returns to the inert capsule',
};

export function expectWords(expected: string | null): string {
  if (expected === null) return 'no expected outcome configured';
  return EXPECT_WORDS[expected] ?? `expected: ${expected}`;
}

/** Whether a recorded check came to what its candidate's role expected. `met` null: the candidate has no role. */
export function checkExpectation(check: CapabilityCheckRecord, roles: CandidateRoleView[]): { met: boolean | null; words: string } {
  const role = roles.find((item) => item.key === check.candidate_key);
  if (role === undefined || role.expect === null) return { met: null, words: 'this Plan has no configured expectation' };
  if (!check.answered) return { met: null, words: 'no answer is recorded yet' };
  const met = check.activation_state === role.expect;
  return {
    met,
    words: met
      ? `as expected for the ${role.role ?? 'configured'} role (${role.expect})`
      : `NOT what the ${role.role ?? 'configured'} role expects: it expects ${role.expect} and the runtime recorded ${check.activation_state}`,
  };
}

/** ValidatePlan's verdict on the exact bytes, in words. */
export function validationWords(check: CapabilityCheckRecord): string {
  const validation = check.validation;
  if (validation === null || validation.verdict === 'not_asked') return 'ValidatePlan: not asked by this runtime';
  if (validation.verdict === 'valid') return 'ValidatePlan: the resident reads these exact bytes as a valid Plan';
  if (validation.verdict === 'invalid') {
    return `ValidatePlan: the resident reads these exact bytes as invalid (reason ${validation.plan_reason_code ?? 'not reported'})`;
  }
  return `ValidatePlan: asked, and no verdict came back (${validation.reason_code ?? 'no reason reported'})`;
}

/** The transaction's state path, in the resident's order, or what stands in its place. Never a path that is not there. */
export function statePathWords(check: CapabilityCheckRecord): string {
  const path = check.state_path;
  if (path === null || path.read === 'not_asked') return 'state path: not read by this runtime';
  if (path.read === 'no_transaction') return 'state path: none, no transaction was opened';
  if (path.read === 'not_in_ring') return 'state path: the resident\'s flight ring no longer holds this transaction';
  if (path.read !== 'ok' || path.states.length === 0) return 'state path: the flight ring could not be read';
  const joined = path.states.join(' > ');
  return path.starts_at_created === false ? `${joined} (the ring no longer holds its start)` : joined;
}
