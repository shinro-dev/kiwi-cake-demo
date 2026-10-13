// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE CARD PER ACTIVATION (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 5.5 and 6.1).
//
// The Cake region draws every activation the mission recorded as its own card, oldest first, and never replaces one.
// A card's words come from the record and from the candidate's served expectation only: the verdict (`model/verdict.ts`),
// the slot's generation before and after, the candidate key, the time, the capsule before and after, and the record's
// `detail`, attributed to the runtime that wrote it. A runtime that serves no expectation (8771 today) gets one quiet line
// saying so.

import type { MissionActivationRecord, MissionCandidate } from './missionRecord';
import { utcStamp } from './utcTime';
import type { UtcStamp } from './utcTime';
import { REFUSALS, activationVerdict, slotChange } from './verdict';
import type { Verdict } from './verdict';

/** A capsule or digest as its first 12 hex characters; the whole value belongs in the title. */
export function capsuleShort(value: string | null | undefined): string | null {
  if (typeof value !== 'string' || value === '') return null;
  return value.replace(/^sha256:/, '').slice(0, 12);
}

export const NO_EXPECTATION_NOTE = 'This runtime serves no expected outcome for this Plan.';

/**
 * WHOSE WORDS THE DETAIL IS. An activation's `detail` is written by the runtime (`demo_runtime/cake_activation.py`): its
 * reading of the resident's answer ("the resident answered ABORTED; ... the answer does not say why"), or its own code
 * for a request that never reached the resident (`admin_socket_absent`). The resident's own answer is the phase and the
 * status. So every place that prints the detail (the Cake card, the timeline's History, Evidence, the Cake tab) labels it
 * with this, and none says it is the resident's.
 */
export const RUNTIME_DETAIL_LABEL = "The runtime's detail";

export interface ActivationCard {
  /** The operation id: the card's identity, which never changes. */
  id: string;
  verdict: Verdict;
  /**
   * Whether the runtime recorded a refusal by Cake (REJECTED_BY_CAKE or REFUSED_BEFORE_TRANSACTION). The Cake region draws
   * every refusal as its most prominent card, whatever expectation is served (verdict 2026-09-27 item 12); its colour is
   * the verdict's.
   */
  refusal: boolean;
  headline: string;
  /** Key, time and capsule: "mobile-demo-swapped, 14:21:45Z, capsule 9600566de6c9 -> 2f34a84c1971". */
  line: string;
  stamp: UtcStamp | null;
  candidate_key: string | null;
  /** The candidate's served label, or null when the runtime serves none. */
  label: string | null;
  /** The quiet line said when the read candidate list serves this candidate with no expectation; null otherwise. */
  note: string | null;
  /** The record's `detail`, attributed to the runtime that wrote it (RUNTIME_DETAIL_LABEL); null when it carries none. */
  answer: string | null;
  capsule_before_title: string | null;
  capsule_after_title: string | null;
}

export function activationCard(activation: MissionActivationRecord, candidates: readonly MissionCandidate[] | null, referenceDay: string | null): ActivationCard {
  const candidate = (candidates ?? []).find((item) => item.key === activation.candidate_key) ?? null;
  const expected = candidate?.expect ?? null;
  const verdict = activationVerdict(activation, expected);
  const before = activation.slot_before ?? null;
  const after = activation.slot_after ?? null;
  const change = slotChange(before, after);
  const generations = `${before?.generation ?? '?'} -> ${after?.generation ?? '?'}`;
  let headline: string;
  if (verdict.id === 'pending') headline = verdict.word;
  else if (change === 'unchanged') headline = `${verdict.word}, slot unchanged ${generations}`;
  else if (change === 'changed') headline = verdict.id === 'refused_slot_changed' ? `${verdict.word} ${generations}` : `${verdict.word}, slot ${generations}`;
  else headline = `${verdict.word}; the slot was not read both times`;

  const stamp = utcStamp(activation.t_wall_utc, referenceDay);
  const parts: string[] = [activation.candidate_key ?? 'no candidate key recorded'];
  if (stamp !== null) parts.push(stamp.text);
  const capsuleBefore = capsuleShort(before?.capsule);
  const capsuleAfter = capsuleShort(after?.capsule);
  if (change === 'unchanged' && capsuleBefore !== null) parts.push(`capsule ${capsuleBefore} unchanged`);
  else if (change === 'changed' && capsuleBefore !== null && capsuleAfter !== null) {
    parts.push(capsuleBefore === capsuleAfter ? `capsule ${capsuleBefore} unchanged` : `capsule ${capsuleBefore} -> ${capsuleAfter}`);
  }
  return {
    id: activation.operation_uuid,
    verdict,
    refusal: REFUSALS.has(activation.activation_state),
    headline,
    line: parts.join(', '),
    stamp,
    candidate_key: activation.candidate_key,
    label: candidate?.label ?? null,
    // THE NOTE NEEDS THE RECORD: said only when the list was read and served this candidate without an expectation. A
    // list not read, read empty, or read without this key says nothing about what the runtime serves for it.
    note: candidates !== null && candidate !== null && expected === null ? NO_EXPECTATION_NOTE : null,
    answer: activation.detail === null || activation.detail === '' ? null : `${RUNTIME_DETAIL_LABEL}: ${activation.detail}`,
    capsule_before_title: before?.capsule ?? null,
    capsule_after_title: after?.capsule ?? null,
  };
}
