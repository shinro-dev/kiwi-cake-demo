// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// CAKE ON THE ROBOT (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 3.3, 5.5, 5.6 and 6.1).
//
// The Cake region's words, from the mission's activations (every one, oldest first, never replaced: T1, C2), the
// candidate list while the mission waits for Cake, the newest signature check (dated, and muted as a whole when an
// earlier runtime session ran it: C11) and the live slot of GET /api/robot with the record that set it. A capability slot
// check's record is not a mission record and is never one of these cards (C1); it is in Diagnostics, Cake.

import type { CapabilityCheckState } from './capabilityCheck';
import type { MissionCandidate, MissionRecord } from './missionRecord';
import type { RobotStatus } from './robotStatus';
import type { SignatureCheckState } from './signatureCheck';
import { liveSlot, liveSlotActiveWords, liveSlotWhen } from './liveSlot';
import { activationCard, capsuleShort } from './activationCard';
import type { ActivationCard } from './activationCard';
import { utcDayOf, utcStamp } from './utcTime';
import type { UtcStamp } from './utcTime';
import { REFUSALS, signatureVerdict, slotChange } from './verdict';
import type { Verdict } from './verdict';

export interface CakeStoryInput {
  mission: MissionRecord | null;
  candidates: readonly MissionCandidate[] | null;
  signature: SignatureCheckState | null;
  /** The runtime session the snapshot names now. */
  runtimeSession: string | null;
  robot: RobotStatus | null;
  capabilityCheck: CapabilityCheckState | null;
  /** When the robot answer on the screen reached this page. Absent: not known, and no activation is compared with it. */
  robotReadAtMs?: number | null;
  /**
   * Why the last mission read failed, or null. With no mission kept, a failed read is said as not read, never as a
   * mission with no activation; `no_mission_recorded` is the runtime saying there is none.
   */
  missionError?: string | null;
  /** Why the last signature check read failed, or null. With nothing kept, said as not read, never as no check. */
  signatureCheckError?: string | null;
  /**
   * THE MISSION ON THE RECORD WHEN IT IS HISTORY'S (spec section 12), with why: `mission` is then null, its activations are
   * not drawn here, and the region says where they are. Absent or null: no such mission.
   */
  past?: { mission: MissionRecord; reason: string } | null;
  /** The day times print without a date; absent, the mission's start day (or today's), as before. */
  referenceDay?: string | null;
}

export interface PendingCandidate {
  key: string;
  label: string;
  digest: string | null;
  digest_title: string | null;
  role: string | null;
  primary: boolean;
}

export interface SignatureCard {
  verdict: Verdict;
  stale: boolean;
  stamp: UtcStamp | null;
  lines: string[];
}

export interface CakeStoryModel {
  cards: ActivationCard[];
  /** What the region says when it draws no card: none in the mission, or the mission not read and why. */
  noCards: string;
  /** The mission waits for Cake: its candidates, one button each. Null otherwise. */
  pending: PendingCandidate[] | null;
  signature: SignatureCard;
  /** Whether the Run button is drawn at all: not while a mission is carried (rule 4.3). */
  signatureRunOffered: boolean;
  slot: { words: string; set_by: string } | null;
}

function carried(mission: MissionRecord | null): boolean {
  return mission !== null && (mission.state === 'RUNNING' || mission.state === 'WAITING_FOR_CAPABILITY');
}

/** The first line of a check from an earlier runtime session (rule 5.6), said the same on the main card and in the Cake tab. */
export function earlierSessionLine(check: { t_wall_utc: string | null; runtime_session: string | null }): string {
  return `Earlier session: ${utcStamp(check.t_wall_utc, null)?.text ?? 'time not recorded'} (runtime session ${check.runtime_session ?? 'not recorded'}). It says nothing about this session.`;
}

/** The main card's one line for a check of an earlier runtime session: none in this one, and where the earlier one is. */
export function earlierCheckPointer(check: { t_wall_utc: string | null; runtime_session: string | null }): string {
  return `No check in this runtime session. The last one, ${utcStamp(check.t_wall_utc, null)?.text ?? 'time not recorded'} (runtime session ${check.runtime_session ?? 'not recorded'}), is in History.`;
}

function countsLine(check: { rows_as_expected: number; rows_not_as_expected: number; rows_error: number; rows_skipped: number; complete: boolean | null }): string {
  return `${String(check.rows_as_expected)} rows as expected, ${String(check.rows_not_as_expected)} not, ${String(check.rows_error)} in error, ${String(check.rows_skipped)} skipped; ${check.complete === true ? 'complete' : 'not complete'}.`;
}

/**
 * What History says of a check from an earlier runtime session (rule 5.6): its session and date, its counts, and a
 * capsule it covered that the live slot carries now, with what the verifier said of it. Null when the newest check is
 * this session's, or there is none.
 */
export function earlierSignatureLines(input: Pick<CakeStoryInput, 'signature' | 'runtimeSession' | 'robot'>): string[] | null {
  const check = input.signature?.checks[input.signature.checks.length - 1] ?? null;
  if (check === null || signatureVerdict(check, input.runtimeSession).id !== 'sig_earlier') return null;
  const lines = [earlierSessionLine(check), countsLine(check)];
  const live = input.robot?.configured === true ? input.robot.capability.capsule : null;
  const rows = live === null ? [] : check.rows.filter((row) => row.capsule === live);
  if (live !== null && rows.length > 0) {
    const said = rows.map((row) => `${row.observed === 'admitted' ? 'admitted' : row.observed === 'refused' ? 'refused' : row.observed} with the ${row.key} key`).join(', ');
    lines.push(`It covered capsule ${capsuleShort(live) ?? live}, which the live slot carries now: ${said}.`);
  }
  return lines;
}

function signatureCard(input: CakeStoryInput, day: string | null): SignatureCard {
  // A READ THAT DID NOT ANSWER IS NOT A SESSION WITH NO CHECK (verifier r2 defect 5).
  if (input.signature === null) {
    const error = input.signatureCheckError ?? null;
    return { verdict: { id: 'sig_unread', word: error === null ? 'not read yet' : `not read: ${error}`, tone: 'mute' }, stale: false, stamp: null, lines: [] };
  }
  const check = input.signature.checks[input.signature.checks.length - 1] ?? null;
  const verdict = signatureVerdict(check, input.runtimeSession);
  if (check === null) return { verdict, stale: false, stamp: null, lines: [] };
  const stamp = utcStamp(check.t_wall_utc, day);
  const counts = countsLine(check);
  if (verdict.id === 'sig_earlier') {
    // HISTORY'S, NOT THE MAIN SCREEN'S (spec section 12; rule 5.6 as amended 2026-09-28): the card says there is no check
    // of this session and where the earlier one is, with its full date. Its counts and the capsule it covered are in
    // History (`earlierSignatureLines`).
    return { verdict, stale: true, stamp, lines: [earlierCheckPointer(check)] };
  }
  return { verdict, stale: false, stamp, lines: [counts] };
}

function slotFooter(input: CakeStoryInput, day: string | null): CakeStoryModel['slot'] {
  // THE NEWEST RECORD BY TIME (browser r2 N1): the robot's reading, or an activation that changed the slot after it.
  const slot = liveSlot({ robot: input.robot, robotReadAtMs: input.robotReadAtMs ?? null, mission: input.mission, day });
  if (slot === null) return null;
  const generation = slot.generation;
  const words = `Live slot: ${slot.name ?? 'the capability slot'}, ${liveSlotActiveWords(slot)}${generation === null ? '' : `, generation ${generation}`}${
    slot.capsule === null ? '' : `, capsule ${capsuleShort(slot.capsule) ?? slot.capsule}`
  }, ${liveSlotWhen(slot)}`;
  if (slot.from.kind === 'activation') {
    return { words, set_by: 'Set by that activation. No robot reading taken after it has reached this page yet.' };
  }
  // WHAT SET IT: the newest activation that CHANGED the slot to this generation (never a refusal, and only when both slot
  // reads answered and differ), else the newest slot check of the current resident session that CHANGED it to this
  // generation (by the same rule: never a refused check, whose slot reads show no change); else nothing on record in this
  // session says.
  const activations = input.mission?.activations ?? [];
  const byActivation =
    generation === null
      ? undefined
      : [...activations]
          .reverse()
          .find(
            (item) =>
              !REFUSALS.has(item.activation_state) && slotChange(item.slot_before, item.slot_after) === 'changed' && item.slot_after?.generation === generation,
          );
  if (byActivation !== undefined) {
    const at = utcStamp(byActivation.t_wall_utc, day);
    return { words, set_by: `Set by the activation of ${byActivation.candidate_key ?? 'a candidate'}${at === null ? '' : ` at ${at.text}`}.` };
  }
  const session = input.capabilityCheck?.current.session_uuid ?? null;
  const byCheck =
    generation === null || session === null
      ? undefined
      : [...(input.capabilityCheck?.checks ?? [])]
          .reverse()
          .find(
            (item) =>
              !REFUSALS.has(item.activation_state) &&
              slotChange(item.slot_before, item.slot_after) === 'changed' &&
              item.slot_after?.generation === generation &&
              item.slot_after.session_uuid === session,
          );
  if (byCheck !== undefined) {
    const at = utcStamp(byCheck.t_wall_utc, day);
    return { words, set_by: `Set by the slot check of ${byCheck.candidate_key ?? 'a Plan'}${at === null ? '' : ` at ${at.text}`}.` };
  }
  // THE LIVE SLOT IS CURRENT; the record that set it may be History's mission. Named with its full date, and where it is.
  const byPast =
    generation === null
      ? undefined
      : [...(input.past?.mission.activations ?? [])]
          .reverse()
          .find((item) => !REFUSALS.has(item.activation_state) && slotChange(item.slot_before, item.slot_after) === 'changed' && item.slot_after?.generation === generation);
  if (byPast !== undefined) {
    const at = utcStamp(byPast.t_wall_utc, null);
    return { words, set_by: `Set by the activation of ${byPast.candidate_key ?? 'a candidate'}${at === null ? '' : ` at ${at.text}`}, in the last mission (in History).` };
  }
  // THE MISSION NOT READ is not a mission whose records say nothing (verifier r2 defect 5).
  if (input.mission === null && (input.past ?? null) === null && (input.missionError ?? null) !== 'no_mission_recorded') {
    return { words, set_by: 'The mission has not been read, so this page cannot say what set it.' };
  }
  return { words, set_by: 'No record of this mission or of this resident session says what set it.' };
}

/** The line of a region with no card, from the mission read (as the outcome card words it). */
function noCardsLine(mission: MissionRecord | null, missionError: string | null, past: CakeStoryInput['past'] = null): string {
  // HISTORY'S MISSION (spec section 12): none of its cards here, and where they are.
  if (mission === null && past !== null) {
    const when = past.reason === 'earlier_day' ? 'today' : 'in this runtime session';
    return past.mission.activations.length === 0 ? `No Cake activation ${when}.` : `No Cake activation ${when}. The last mission's activations are in History.`;
  }
  if (mission !== null || missionError === 'no_mission_recorded') return 'No Cake activation in this mission.';
  if (missionError === null) return 'The mission has not been read yet.';
  return `The mission has not been read: ${missionError}.`;
}

export function cakeStory(input: CakeStoryInput): CakeStoryModel {
  const mission = input.mission;
  const day = input.referenceDay === undefined ? (utcDayOf(mission?.started_wall_utc ?? null) ?? new Date().toISOString().slice(0, 10)) : input.referenceDay;
  const cards = (mission?.activations ?? []).map((activation) => activationCard(activation, input.candidates, day));
  const waiting = mission !== null && mission.state === 'WAITING_FOR_CAPABILITY' && mission.waiting_reason === 'capability_unavailable';
  const rolesServed = (input.candidates ?? []).some((item) => (item.role ?? null) !== null);
  const pending = !waiting
    ? null
    : (input.candidates ?? []).map((item) => ({
        key: item.key,
        label: item.label ?? item.key,
        digest: capsuleShort(item.payload_digest),
        digest_title: item.payload_digest,
        role: item.role ?? null,
        primary: rolesServed && item.role === 'activate',
      }));
  return { cards, noCards: noCardsLine(mission, input.missionError ?? null, input.past ?? null), pending, signature: signatureCard(input, day), signatureRunOffered: !carried(mission), slot: slotFooter(input, day) };
}
