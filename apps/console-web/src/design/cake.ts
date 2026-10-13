// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// CAKE, AS IT ACTUALLY IS.
//
// The imported design's right-hand column is the richest thing on the screen: a capability request card with two
// selectable candidates (`mobile_approach 0.1.0-dev`, `mobile_approach 0.4.0`), a validating bar, an activation
// receipt, and a four-row module inventory carrying `v1.2.0`, `v0.9.3`, `v0.4.0` and `v0.3.1`. NONE OF THAT EXISTS.
// It was drawn to show what the integration will look like, and the design says so itself, twice: the dock is
// labelled "sample media, illustrative values marked" and the inventory's own footer reads "Prototype inventory,
// module identities illustrative".
//
// WHAT THE RUNTIME ACTUALLY REPORTS, verified rather than assumed:
//
//   `cake_view()` returns `slots: []`, `transactions: []`, `update_preview: None`, `continuity: None` on every
//   snapshot, as literals. No code path can populate them.
//   `mission_participation` is the constant `(False, "the controller has no Cake adapter")`.
//   `modules_active` is null or False and has no branch that produces true.
//   `components()` never emits a component whose layer is `cake_module`, and a runtime test pins that absence.
//   ONE capability mentions Cake: `cake_update`, state `disabled`, reason_code `not_built`, reason "nothing connects
//   this runtime to a resident", dependency `cake_resident`, planned. The Diagnostics drawer lists it with the other
//   pre-declared controls (`panels/SystemPanels.tsx`), pointed at the capability slot check card, which is the live
//   module update this demo performs. This file used to model an "Update a module" operation from it as well; nothing
//   ever drew that model, and a control that is always off is not kept where nothing shows it.
//
// So this file renders the ladder and the runtime's real component list. It invents no candidate, no version and no
// receipt. The decision log is explicit about why: "Use the fields Cake actually has; do not add a semantic version to
// satisfy a mockup." A module row whose version the runtime does not report says that, in the words the design's own
// binding table already specifies for the case.
//
// ONE OF THOSE ABSENCES IS NOW REAL, AND IT IS NARROWER THAN THE DRAWING. A runtime that carries a MISSION serves a
// prepared candidate list and takes an activation request, on the mission surface and NOT through contract v1, whose
// eight command types still include no Cake operation. The candidates are the runtime's own and the request is one
// opaque key; `activationLine` below is where the states that come back are given words, and it is the only
// place in this application that decides which of them may be drawn as an activation. Everything above this paragraph
// is still true of the snapshot's own `cake` object, which is what the ladder and the module rows are built from.

import type { CakeLadderRung, CakeView, Component } from '@kiwi/demo-contracts';
import type { CakeFinding, CakeStatus } from '../api/types';
import { cakeTaskSentence } from '../model/cakeTask';
import type { MissionActivationRecord, MissionCandidate, MissionRecord, SlotReading } from '../model/missionRecord';
import { RUNTIME_DETAIL_LABEL, activationCard } from '../model/activationCard';
import { TONE_COLOUR } from './TopBar';
import type { ViewState } from '../model/types';
import { C, INK, wash } from './theme';

export interface LadderRow {
  key: string;
  label: string;
  value: boolean | null;
  /** The runtime's own evidence for the rung. Never paraphrased. */
  evidence: string;
  tone: string;
  words: string;
}

/** The five rungs in the order the contract declares them: lowest first, and a higher one never implies a lower one. */
const RUNGS: [keyof Omit<CakeView['ladder'], 'synthetic'>, string][] = [
  ['artifacts_available', 'Artifacts available'],
  ['resident_reachable', 'Resident reachable'],
  ['admin_interface_compatible', 'Admin interface compatible'],
  ['modules_active', 'Modules active'],
  ['mission_participation', 'Taking part in this task'],
];

function rungRow(key: string, label: string, rung: CakeLadderRung): LadderRow {
  const value = rung.value;
  return {
    key,
    label,
    value,
    evidence: rung.evidence,
    tone: value === true ? C.ok : value === false ? C.mute : C.warn,
    // Null is not a no. It is the runtime saying it did not establish this, which is a third answer and is printed
    // as one: three rungs of this ladder can legitimately be null at the same time.
    words: value === true ? 'yes' : value === false ? 'no' : 'not determined',
  };
}

export interface CakeChip {
  label: string;
  dot: string;
  fg: string;
  bg: string;
  border: string;
}

export function cakeChip(cake: CakeView): CakeChip {
  const participation = cake.ladder.mission_participation.value;
  if (participation === true) {
    return { label: 'Participating in this task', dot: C.ok, fg: '#a7f3d0', bg: wash(C.ok, 0.1), border: wash(C.ok, 0.32) };
  }
  if (participation === false) {
    return { label: 'Not connected to this task', dot: INK.faint, fg: INK.quiet, bg: '#13131a', border: 'var(--color-border)' };
  }
  return { label: 'Not determined', dot: C.warn, fg: '#fde68a', bg: wash(C.warn, 0.1), border: wash(C.warn, 0.3) };
}

export interface ModuleRow {
  id: string;
  name: string;
  /** The runtime's own version, or the sentence for a component that reports none. */
  version: string;
  versionKnown: boolean;
  /** `active` and `health` are separate facts and stay separate. */
  stateLabel: string;
  tone: string;
  evidence: string;
  layer: Component['layer'];
}

/**
 * WHICH SOFTWARE IS ACTUALLY EXECUTING, from the runtime's own component list.
 *
 * This replaces the design's four illustrative modules entirely. `active` is `boolean | null` and `health` is a
 * separate enum, so a component that is installed and reachable is never drawn as one that is executing this task.
 */
export function moduleRows(components: Component[]): ModuleRow[] {
  return components.map((component) => ({
    id: component.id,
    name: component.role,
    version: component.version ?? 'version not reported',
    versionKnown: component.version !== null,
    stateLabel:
      component.active === true
        ? `executing, ${component.health}`
        : component.active === false
          ? `not executing, ${component.health}`
          : `execution not stated, ${component.health}`,
    tone:
      component.health === 'ok' && component.active === true
        ? C.ok
        : component.health === 'failed'
          ? C.bad
          : component.health === 'degraded'
            ? C.warn
            : C.mute,
    evidence: component.evidence,
    layer: component.layer,
  }));
}

/**
 * THE RESIDENT LINE OF THE SIDEBAR: who answered, and how current that answer is.
 *
 * Every field comes from the runtime's structured status. Nothing here reads a ladder rung's evidence sentence: that
 * prose is written for an operator and is not a data format, and a screen that scraped it would break the first time
 * one was reworded.
 */
export interface ResidentLine {
  /** What the compact sidebar says in one line. Never a version or a module name this console was not told. */
  headline: string;
  /** Why that is what it says, in one short sentence. */
  detail: string;
  tone: string;
  /** True when what is shown was retained from a read that is no longer current. */
  stale: boolean;
  /** How old the reading is, already worded, or null when there is no reading to be old. */
  age: string | null;
  /** True once a different resident session has answered, which is what a restart looks like from here. */
  restarted: boolean;
}

/** How long ago, in words. */
export function agedWords(ageMs: number): string {
  if (ageMs < 1000) return 'just now';
  const seconds = Math.floor(ageMs / 1000);
  if (seconds < 60) return `${String(seconds)}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${String(minutes)}m ago`;
  return `${String(Math.floor(minutes / 60))}h ago`;
}

/**
 * The sidebar's resident line, from the runtime's separately versioned status.
 *
 * FOUR STATES, KEPT APART: nothing configured; configured and never reached; answering; and answered once and no
 * longer answering. The last is the one that has to be right, and it is the reason this surface exists: it shows what
 * the resident last reported, says how old that is, and never redraws it as a resident holding nothing.
 */
export function residentLine(status: CakeStatus | null, error: string | null): ResidentLine {
  if (status === null) {
    return {
      headline: 'No resident reading',
      detail: error ?? 'this runtime has not answered about a Cake resident yet',
      tone: C.mute, stale: false, age: null, restarted: false,
    };
  }
  const restarted = status.session_changed === true;
  if (!status.configured) {
    return {
      headline: 'No resident configured',
      detail: 'this runtime was not told where a resident is, so none was asked',
      tone: C.mute, stale: false, age: null, restarted: false,
    };
  }
  const observation = status.observation;
  if (observation === null) {
    return {
      headline: 'Resident not reached',
      detail: `configured, and no resident has answered: ${status.reason_code ?? 'no reason was given'}`,
      tone: C.warn, stale: false, age: null, restarted,
    };
  }
  const age = agedWords(observation.age_ms);
  if (status.stale) {
    return {
      // A RESIDENT THAT WENT AWAY DID NOT BECOME AN EMPTY ONE. What is on the screen is the last thing it said, and
      // the line says so in its first two words rather than in a tooltip.
      headline: 'Resident last seen',
      detail: `contact lost (${status.reason_code ?? 'no reason was given'}); showing its last answer`,
      tone: C.warn, stale: true, age, restarted,
    };
  }
  return {
    headline: 'Resident answering',
    detail: restarted
      ? 'a different resident session than the one this runtime first saw'
      : 'a resident answered this runtime read-only; this surface does not say which machine it runs on',
    tone: C.ok, stale: false, age, restarted,
  };
}

/* ---- what one prepared activation came to ------------------------------------------------------------------------ */

/**
 * THE ACTIVATION STATES, AND THE THREE THINGS THEY MUST NEVER BE CONFLATED INTO.
 *
 * This is the whole reason this vocabulary is decided in one place instead of at four call sites:
 *
 *   REQUEST_FAILED IS NOT A REJECTION. The request did not land, so the resident judged nothing and opened no
 *   transaction. Wording it as Cake refusing anything states a verdict nobody reached, about a candidate nobody read.
 *
 *   REJECTED_BY_CAKE IS AN ABORTED TRANSACTION, AND NO CAUSE IS STATED FOR IT. The resident's answer carries the phase
 *   and not a reason, and aborts of an admitted candidate have been MEASURED on this deployment: the same prepared Plan
 *   completed on a freshly started resident and aborted once the resident had been up past its transaction deadline
 *   (docs/autonomous-demo/cake-console-handoff-2026-09-23.md, section 8). This used to say the cause was a candidate
 *   the resident had not admitted, which that measurement contradicts. It is NOT an upload that failed, NOT a
 *   signature that did not verify and NOT a corrupted binary either: nothing in the record says any of those, and each
 *   of them would send somebody to look for a fault that is not there.
 *
 *   OBSERVED IS THE ONLY ONE THAT MEANS ACTIVATED. It is the slot read back, carrying the capability. A SUBMITTED
 *   activation is pending however far its transaction got, and a transaction the runtime reports as completed is NOT
 *   an activation on its own: the read-back is the evidence and the phase is not.
 *
 * A FIFTH STATE ARRIVED LATER, and it is the third rule made explicit by the runtime. COMPLETED_NOT_OBSERVED is a
 * transaction that completed while the slot, read back afterwards, did not carry the capability. It used to be recorded
 * as SUBMITTED, so "in flight" and "finished without moving the slot" read the same; SUBMITTED now means in flight and
 * nothing else. It is not an activation, and it says so.
 */
export type ActivationVerdict =
  | 'pending'
  | 'not_delivered'
  | 'aborted'
  | 'refused_before_transaction'
  | 'not_observed'
  | 'activated'
  | 'unknown';

export interface ActivationLine {
  verdict: ActivationVerdict;
  /** The few words the screen leads with. */
  headline: string;
  /** What it means, with nothing in it the record does not support. */
  sentence: string;
  tone: string;
  /**
   * True for OBSERVED and for nothing else. Every place that draws an activation as done reads THIS, so a transaction
   * phase can never promote a pending activation into a finished one.
   */
  activated: boolean;
  /**
   * The note that keeps a transaction phase from being read as an outcome. Present whenever the record carries a phase
   * and the slot has not been read back.
   */
  qualifier: string | null;
}

/**
 * A refusal's headline and colour are the demo screen's card words (spec 5.1, audit C10): "Cake refused the successor,
 * slot unchanged 1 -> 1" in the neutral bright ink when the runtime serves no expectation for the candidate (verdict
 * 2026-09-27 item 12), "Refused as expected" in the ok tone when it serves the refusal as expected, and never red or amber
 * without an expectation that says so.
 */
function refusalWords(activation: MissionActivationRecord, candidates: readonly MissionCandidate[] | null): { headline: string; tone: string } {
  const card = activationCard(activation, candidates, null);
  return { headline: card.headline, tone: TONE_COLOUR[card.verdict.tone] };
}

export function activationLine(activation: MissionActivationRecord, candidates: readonly MissionCandidate[] | null = null): ActivationLine {
  const phase = activation.transaction_phase;
  // THE RUNTIME'S OWN WORDS, SAID TO BE ITS OWN. They are carried unaltered, after a sentence of their own that names
  // whose they are; spliced in bare after a full stop they read as this console's sentence, and in lower case.
  const detail = activation.detail === null ? '' : ` ${RUNTIME_DETAIL_LABEL}: ${activation.detail.replace(/\.$/, '')}.`;
  // A phase is a statement about the transaction and never about the slot. It is shown, and it is qualified, for
  // every state but the one that read the slot back.
  const qualifier =
    phase === null || activation.activation_state === 'OBSERVED'
      ? null
      : `The transaction reports the phase ${phase}. A phase is not an activation: only a read-back of the slot says the capability is there.`;

  switch (activation.activation_state) {
    case 'OBSERVED':
      return {
        verdict: 'activated',
        headline: 'Activated',
        sentence: `The slot was read back and carries the capability.${detail}`,
        // THE CARD'S COLOUR (should_fix honesty S4): green only with a served expectation it met, the bright ink without.
        tone: TONE_COLOUR[activationCard(activation, candidates, null).verdict.tone],
        activated: true,
        qualifier: null,
      };
    case 'REJECTED_BY_CAKE':
      return {
        verdict: 'aborted',
        headline: refusalWords(activation, candidates).headline,
        // WHAT THE RESIDENT SAID, AND NO CAUSE. Its answer says the ApplyPlan transaction aborted and not why, so any
        // reason printed after this is the runtime's reading of that answer and is attributed to it.
        sentence:
          'The resident aborted the ApplyPlan transaction that carried the prepared candidate, and nothing was activated. ' +
          `Its answer says that the transaction aborted and not why; a cause written beside it is the runtime's reading, not the resident's.${detail}`,
        tone: refusalWords(activation, candidates).tone,
        activated: false,
        qualifier,
      };
    case 'REFUSED_BEFORE_TRANSACTION':
      return {
        verdict: 'refused_before_transaction',
        headline: refusalWords(activation, candidates).headline,
        // CAKE JUDGED THE CANDIDATE. The resident answered with a refusal class and opened no transaction, so the slot
        // was not touched. An integrity refusal (a Plan whose bytes do not hold their digests) is never a signature one.
        sentence:
          'The resident refused the prepared candidate before opening any transaction, so no transaction was opened and the slot ' +
          `was not touched. That is Cake judging the candidate, not a request that failed to arrive.${detail}`,
        tone: refusalWords(activation, candidates).tone,
        activated: false,
        qualifier: null,
      };
    case 'REQUEST_FAILED':
      return {
        verdict: 'not_delivered',
        headline: 'The request did not land',
        sentence:
          'This request did not reach the resident, so nothing was judged and no transaction was opened. It says ' +
          `nothing about the candidate.${detail}`,
        tone: C.warn,
        activated: false,
        qualifier,
      };
    case 'SUBMITTED':
      return {
        verdict: 'pending',
        headline: 'Submitted, not yet activated',
        // IN FLIGHT, and nothing else: no answer has come back, or the resident says the transaction is still running.
        sentence:
          'The operation is in flight: the runtime wrote it down and has no final answer for it yet, and the slot has not ' +
          `been read back since. The runtime refuses a second activation for this mission until it is answered.${detail}`,
        tone: C.wait,
        activated: false,
        qualifier,
      };
    case 'COMPLETED_NOT_OBSERVED':
      return {
        verdict: 'not_observed',
        headline: 'Completed, capability not in the slot',
        sentence:
          'The transaction completed, and the slot read back afterwards does not carry the capability, so nothing was ' +
          `activated by it. The mission is still held and a prepared candidate can be activated again.${detail}`,
        tone: C.warn,
        activated: false,
        qualifier,
      };
    default:
      return {
        verdict: 'unknown',
        headline: activation.activation_state,
        sentence:
          'This console has no words for that activation state, so the runtime\'s own word is printed. It is not ' +
          `read as an activation: only a slot read back is.${detail}`,
        tone: C.mute,
        activated: false,
        qualifier,
      };
  }
}

/**
 * THE FACTS OF ONE ACTIVATION RECORD ON ONE LINE: its state, the transaction's phase, the runtime's word for the result,
 * the handle the resident gave the transaction, the epoch its answer printed, and whether the mission was asked to stop
 * while it was in flight. Each is the record's own token; an absent one says so, and the handle and the epoch are left
 * out only when the record has no transaction part to carry them.
 *
 * Like the row below, it DECIDES NOTHING: the verdict is `activationLine`'s.
 */
export function activationFactsLine(activation: MissionActivationRecord): string {
  const parts = [
    `state ${activation.activation_state}`,
    `phase ${activation.transaction_phase ?? 'not reported'}`,
    `result ${activation.outcome ?? 'not reported'}`,
  ];
  const transaction = activation.transaction ?? null;
  if (transaction !== null) {
    parts.push(`transaction handle ${transaction.transaction_handle ?? 'not reported'}`);
    parts.push(`answer epoch ${transaction.answer_epoch ?? 'not reported'}`);
  }
  if (activation.stopped_during_activation === true) parts.push('the mission was asked to stop while this was in flight');
  return parts.join(' · ');
}

/**
 * ONE SLOT READING, AS WORDS FOR A ROW: what the capability slot carried when the runtime read it, before an activation
 * or after its answer. Each value is the resident's own, as text; an absent one says so and is never a zero. A read that
 * failed or did not find the slot is said to be that, and its fields are not drawn as a slot that holds nothing.
 *
 * This DECIDES NOTHING. Whether the capability is there is `activationLine`'s question and the record's verdict; the row
 * only shows the evidence the verdict came from, so an operator can see the capsule and the generation move, or not.
 */
export interface SlotReadingRow {
  /** How the read went, in words. */
  read: string;
  /** True when the read answered and named the slot, so the fields below are the slot's. */
  answered: boolean;
  state: string;
  capsule: string;
  generation: string;
  instance: string;
  epoch: string;
  /** Which resident answered the read, and under which Plan. Shown where every record is shown whole (Evidence). */
  session: string;
  plan: string;
}

export function slotReadingRow(reading: SlotReading | null | undefined): SlotReadingRow {
  const absent = 'not reported';
  if (reading === null || reading === undefined) {
    return {
      read: 'no reading recorded',
      answered: false,
      state: absent,
      capsule: absent,
      generation: absent,
      instance: absent,
      epoch: absent,
      session: absent,
      plan: absent,
    };
  }
  const read =
    reading.read === 'ok'
      ? 'read back'
      : reading.read === 'slot_absent'
        ? 'the listing answered and did not name the slot'
        : reading.read === 'failed'
          ? 'the read failed; this is not an empty slot'
          : `read ${reading.read ?? 'not stated'}`;
  const answered = reading.read === 'ok';
  return {
    read,
    answered,
    state: reading.state ?? absent,
    capsule: reading.capsule ?? absent,
    generation: reading.generation ?? absent,
    instance: reading.instance ?? absent,
    epoch: reading.epoch ?? absent,
    session: reading.session_uuid ?? absent,
    plan: reading.plan_digest ?? absent,
  };
}

export { cakeTaskSentence };

/**
 * THE MISSION'S PREPARED CANDIDATE A PRESS WOULD SEND: the operator's pick while it is offered, else none. The same rule
 * as the capability slot check (`checkChoice`): the runtime sorts its keys, so a first-offered default was the abort-swap
 * candidate, and nothing is ever preselected.
 */
export function missionCandidateChoice(candidates: readonly { key: string }[] | null, chosen: string | null): string | null {
  return chosen !== null && (candidates ?? []).some((item) => item.key === chosen) ? chosen : null;
}

export interface CakeModel {
  chip: CakeChip;
  ladder: LadderRow[];
  modules: ModuleRow[];
  /**
   * The one sentence the rest of this console uses for the same fact (`model/cakeTask.ts`), read from the mission
   * record. It WAS the constant, pinned by `design/screen.test.ts` so that wiring up a mission could not change it as a
   * side effect; the one-block demo is a mission that waits on a Cake activation and resumes through one, and the
   * constant was false for the whole of it. The pin now holds the three readings and the reason for each.
   */
  taskSentence: string;
  /**
   * True while nothing here was read from a resident. It is the runtime's own flag and it is shown, because a panel
   * that looks like an inventory and is not one has to say which it is.
   */
  synthetic: boolean;
  /**
   * How many slots the RESIDENT reported, or null when this console holds no reading of one.
   *
   * It used to be `cake.slots.length`, the contract's slot LIST, which the runtime never populates because get-status
   * reports a count and not a per-slot inventory. That length is zero whatever the resident holds, so the sidebar
   * stated "0 resident slots" as a fact about the resident when it was a fact about an empty array. The count now
   * comes from the status surface, and having no reading is null rather than zero.
   */
  slotCount: number | null;
  /**
   * How many ladder rungs the runtime ESTABLISHED, which is how many measured true. A rung it could not determine is
   * not counted as established and is not counted as refuted either; the drawer shows all three answers per rung.
   */
  established: number;
  /** Who answered and how current that is. See ResidentLine. */
  resident: ResidentLine;
  /** Whether anything is RUNNING in the resident, as the runtime put it. Null far more often than it is false. */
  activity: CakeFinding | null;
  /** The whole structured status, for the Connections view. The main screen never draws it as the robot's. */
  status: CakeStatus | null;
}

export function cakeModel(view: ViewState, status: CakeStatus | null = null, statusError: string | null = null, mission: MissionRecord | null = null): CakeModel {
  const cake = view.cake;
  const ladder = RUNGS.map(([key, label]) => rungRow(key, label, cake.ladder[key]));
  return {
    chip: cakeChip(cake),
    ladder,
    established: ladder.filter((rung) => rung.value === true).length,
    modules: moduleRows(view.components),
    taskSentence: cakeTaskSentence(mission),
    synthetic: cake.synthetic,
    slotCount: status?.observation?.counts.slots ?? null,
    resident: residentLine(status, statusError),
    activity: status?.activity ?? null,
    status,
  };
}
