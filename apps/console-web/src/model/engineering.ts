// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The rules behind the engineering view, kept out of the render so they can be tested as what they are: statements
// about what may and may not be said from the records.
//
// Four of them run through this whole file.
//
//   NO STATISTIC BEFORE A SAMPLE. A figure computed over an empty list is not a measurement of zero, it is the
//   absence of a measurement, and the panel draws nothing rather than a number nobody took.
//
//   TWO CLOCKS ARE NEVER ONE. A duration measured on the robot's computer and a duration measured on this one are
//   never added, never compared and never placed on the same axis. Where a recording mixes them, the records outside
//   the axis clock are stepped past one at a time rather than given a time they do not have.
//
//   A COUNT IS NOT A DURATION. Where the only comparable thing between two sequence numbers is how many samples lie
//   between them, that is what is stated, and it is never turned into milliseconds.
//
//   ABSENT IS NOT A DEFAULT. A run that did not record what it ran against is grouped as a run that did not record
//   it, never folded in with the mode this runtime happens to be in now.

import type { DataMode, SnapshotTimings, SourceStamp } from '@kiwi/demo-contracts';
import type { HistoryEntry } from '../api/types';
import type { ContractEvent } from '../contracts/envelope';
import type { TapView } from './sources';
import type { MeasuredStateView, TimingSpanView, ViewState } from './types';

/* ---- timing ------------------------------------------------------------------------------------------------- */

/**
 * Which aggregate a timing table is drawn from. They cover different windows and are never merged: the runtime
 * aggregates every record of the run, and the fold holds only what reached this tab, which is less whenever a tab was
 * opened late, reconnected, or read a runtime whose event ring had already turned over.
 */
export type TimingBasis = 'runtime' | 'this fold';

export interface TimingTable {
  basis: TimingBasis;
  /** The run the runtime measured these in, when it named one. The fold does not name a run here. */
  run_id: string | null;
  rows: TimingSpanView[];
}

function spansOf(timings: SnapshotTimings): TimingSpanView[] {
  return timings.spans.map((span) => ({
    span: span.span,
    count: span.count,
    total_ns: span.total_ns,
    last_ns: span.last_ns,
    max_ns: span.max_ns,
    clock_domain: span.clock_domain,
    first_seq: span.first_seq,
    last_seq: span.last_seq,
  }));
}

function orderSpans(rows: TimingSpanView[]): TimingSpanView[] {
  return rows.slice().sort((a, b) => (a.span === b.span ? a.clock_domain.localeCompare(b.clock_domain) : a.span.localeCompare(b.span)));
}

/**
 * The timing table to draw, or NULL when nothing has been measured at all.
 *
 * Null is the important answer: it is what a run that has measured no span looks like, and it is what the panel draws
 * as the sentence saying so rather than as a total of zero milliseconds.
 */
export function timingTable(view: ViewState): TimingTable | null {
  const runtime = view.runtime_timings;
  if (runtime !== null && runtime.spans.length > 0) {
    return { basis: 'runtime', run_id: runtime.run_id, rows: orderSpans(spansOf(runtime)) };
  }
  if (view.timings.length > 0) return { basis: 'this fold', run_id: null, rows: orderSpans(view.timings) };
  return null;
}

/** How many clock domains a table covers. Two is not a fault; pooling them would be. */
export function clockDomainsOf(rows: TimingSpanView[]): string[] {
  return [...new Set(rows.map((row) => row.clock_domain))].sort((a, b) => a.localeCompare(b));
}

/* ---- the tap and the sample a decision consumed ------------------------------------------------------------- */

/**
 * What can be said about the latest tap sample this tab holds and the sample the controller actually acted on.
 *
 * The one thing that cannot be said is how far apart they are in TIME. Both stamps are monotonic readings and they
 * belong to their own session's clock; subtracting them would produce a duration nobody measured. Inside one session
 * the sequence numbers count the same thing, so a difference between them is a number of samples and is stated as
 * that. Across two sessions even the sequence numbers count different things, and the answer is that there is no
 * comparison to make.
 */
export type TapComparison =
  | { kind: 'absent'; missing: 'tap' | 'consumed' | 'both' }
  | { kind: 'not_comparable'; tap_session: string; consumed_session: string }
  | { kind: 'same_sample'; session: string; seq: number }
  | { kind: 'controller_behind'; session: string; tap_seq: number; consumed_seq: number; samples: number }
  | { kind: 'tab_behind'; session: string; tap_seq: number; consumed_seq: number; samples: number };

export function compareTapWithConsumed(tap: SourceStamp | null, consumed: SourceStamp | null): TapComparison {
  if (tap === null && consumed === null) return { kind: 'absent', missing: 'both' };
  if (tap === null) return { kind: 'absent', missing: 'tap' };
  if (consumed === null) return { kind: 'absent', missing: 'consumed' };
  if (tap.session !== consumed.session) {
    return { kind: 'not_comparable', tap_session: tap.session, consumed_session: consumed.session };
  }
  if (tap.seq === consumed.seq) return { kind: 'same_sample', session: tap.session, seq: tap.seq };
  const samples = Math.abs(tap.seq - consumed.seq);
  const kind = tap.seq > consumed.seq ? 'controller_behind' : 'tab_behind';
  return { kind, session: tap.session, tap_seq: tap.seq, consumed_seq: consumed.seq, samples };
}

/** The sentence for a comparison. It names samples, never milliseconds, and never a clock the record did not carry. */
export function comparisonWords(comparison: TapComparison): string {
  switch (comparison.kind) {
    case 'absent':
      if (comparison.missing === 'both') return 'Neither a tap sample nor a consumed sample has arrived in this tab.';
      if (comparison.missing === 'tap') return 'No tap sample has arrived in this tab, so there is nothing to compare the consumed one with.';
      return 'The controller has not recorded a consumed sample yet, so there is nothing to compare the tap with.';
    case 'not_comparable':
      return `Not comparable: the tap is reading source session ${comparison.tap_session} and the controller consumed ${comparison.consumed_session}. Two sessions count their samples separately and the two sequence numbers are not the same measurement.`;
    case 'same_sample':
      return `The same sample: session ${comparison.session}, sequence ${String(comparison.seq)}.`;
    case 'controller_behind':
      return `The controller acted on a sample ${String(comparison.samples)} behind the latest this tab holds, in session ${comparison.session}. That is a count of samples and not a delay: the two stamps are monotonic readings and are never subtracted.`;
    case 'tab_behind':
      return `This tab's latest tap sample is ${String(comparison.samples)} behind the one the controller acted on, in session ${comparison.session}. That is a count of samples and not a delay: the two stamps are monotonic readings and are never subtracted.`;
  }
}

/**
 * The joint names to draw, taken from the records rather than from a list written here.
 *
 * The contract requires the six, so they are the order when they are present; a seventh joint a newer controller
 * reports is appended as itself instead of being dropped by a literal this file would have to be edited to extend.
 */
export const CONTRACT_JOINTS: readonly string[] = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper'];

export function jointNames(tap: TapView | null, consumed: MeasuredStateView | null): string[] {
  const seen = new Set<string>([...Object.keys(tap?.joints ?? {}), ...Object.keys(consumed?.joints ?? {})]);
  const extra = [...seen].filter((name) => !CONTRACT_JOINTS.includes(name)).sort((a, b) => a.localeCompare(b));
  return [...CONTRACT_JOINTS, ...extra];
}

/**
 * WHAT A JOINT NUMBER IS IN, AND WHY THIS CONSOLE WILL NOT SAY.
 *
 * The contract records arm joints in the host's position units as consumed and declares no unit field, so no record
 * reaching this screen states one. The one place in the pipeline that spells a unit is a key in the observation
 * store, and it is wrong for one of the six: the controller labels its own gripper measurements in gripper units,
 * not degrees. A column that printed `deg` from that key would be asserting something no record says and one record
 * contradicts, so every joint reads as not stated until a record states it.
 */
export const JOINT_REPRESENTATION_UNSTATED = 'not stated';

export const JOINT_REPRESENTATION_NOTE =
  'No record states what a joint number is in. The contract keeps arm joints in the host position units as consumed and declares no unit, and the one key in this system that spells a unit is wrong for the gripper, which the controller measures in gripper units and not in degrees. So the representation reads not stated rather than being copied off a field name.';

/* ---- finished runs, grouped by what produced them ------------------------------------------------------------ */

/**
 * One group of finished runs: the same implementation, run in the same mode.
 *
 * The mode is part of the key because a pass on the robot and a pass against the synthetic source are not the same
 * evidence, and the digest is part of it because two runs of different builds are not a series. `data_mode: null` is
 * a row whose journal was written before the mode was recorded; it is its own group and is never filled in from the
 * mode this runtime is in now, which is exactly how a synthetic run would later be read as a hardware one.
 */
export interface RunGroup {
  key: string;
  data_mode: DataMode | null;
  /** The bundle the run was executed from, which is the identity of the implementation. Null when none was recorded. */
  implementation: string | null;
  entries: HistoryEntry[];
}

const MODE_ORDER: Record<string, number> = { live: 0, replay: 1, synthetic: 2 };

function implementationOf(entry: HistoryEntry): string | null {
  return entry.bundle_digest ?? entry.artifact_digest ?? null;
}

export function groupRuns(entries: HistoryEntry[]): RunGroup[] {
  const groups = new Map<string, RunGroup>();
  for (const entry of entries) {
    const mode = entry.data_mode ?? null;
    const implementation = implementationOf(entry);
    const key = `${mode ?? 'not recorded'}/${implementation ?? 'not recorded'}`;
    const held = groups.get(key);
    if (held === undefined) {
      groups.set(key, { key, data_mode: mode, implementation, entries: [entry] });
      continue;
    }
    held.entries.push(entry);
  }
  return [...groups.values()].sort((a, b) => {
    const left = a.data_mode === null ? 9 : (MODE_ORDER[a.data_mode] ?? 8);
    const right = b.data_mode === null ? 9 : (MODE_ORDER[b.data_mode] ?? 8);
    if (left !== right) return left - right;
    return (a.implementation ?? '').localeCompare(b.implementation ?? '');
  });
}

/** How a group is titled. A group that recorded neither fact says so in words rather than showing an empty heading. */
export function runGroupWords(group: RunGroup): string {
  const mode =
    group.data_mode === null
      ? 'mode not recorded'
      : group.data_mode === 'live'
        ? 'recorded from hardware'
        : group.data_mode === 'synthetic'
          ? 'recorded from the synthetic source'
          : 'recorded from a replay';
  const implementation = group.implementation === null ? 'implementation not recorded' : `implementation ${digest12(group.implementation)}`;
  return `${mode}, ${implementation}`;
}

export function digest12(value: string | null): string {
  if (value === null) return 'not recorded';
  const bare = value.startsWith('sha256:') ? value.slice('sha256:'.length) : value;
  return bare.slice(0, 12);
}

/* ---- replay: the recording's own provenance and its own clock ------------------------------------------------ */

/**
 * What a recording says about itself, read from the recording and from nowhere else.
 *
 * `data_mode` comes from the run's own `run.started` record. A recording that carries none is a recording written
 * before the field existed, and it stays null: the mode this runtime is in now says nothing about the run that was
 * recorded then, and a hardware pass read later as a simulation is exactly the confusion this exists to prevent.
 */
export interface RecordedProvenance {
  data_mode: DataMode | null;
  /** The build the records were produced by, from the envelope. Null when no record named one. */
  implementation: string | null;
  producer: string | null;
  started_wall_utc: string | null;
}

export function recordedProvenance(events: readonly ContractEvent[]): RecordedProvenance {
  const started = events.find((event) => event.kind === 'run.started');
  const first = events[0] ?? null;
  const withDigest = events.find((event) => event.producer.artifact_digest !== null) ?? null;
  return {
    data_mode: started === undefined ? null : started.payload.data_mode,
    implementation: withDigest?.producer.artifact_digest ?? null,
    producer: first?.producer.name ?? null,
    started_wall_utc: started?.t_wall_utc ?? first?.t_wall_utc ?? null,
  };
}

export function provenanceWords(provenance: RecordedProvenance): string {
  if (provenance.data_mode === 'live') return 'recorded from hardware';
  if (provenance.data_mode === 'synthetic') return 'recorded from the synthetic source';
  if (provenance.data_mode === 'replay') return 'recorded from a replay';
  return 'the recording does not state what it ran against';
}

/**
 * The recording's own time axis.
 *
 * The axis is in ONE clock: the domain of the recording's first record. Records from another domain are counted and
 * named, and they are not placed on it, because the offset between two monotonic clocks on two machines is not known
 * to this console. `elapsed_ns` is how far the cursor has advanced along that axis and `span_ns` is the whole
 * recording; both are null until two records in the axis domain exist, because one record is a moment, not a span.
 */
export interface ReplayAxis {
  records: number;
  folded: number;
  clock_domain: string | null;
  /** How many of the records are on the axis, and how many carry another clock. */
  on_axis: number;
  off_axis: number;
  elapsed_ns: number | null;
  span_ns: number | null;
}

function axisDomain(events: readonly ContractEvent[]): string | null {
  return events[0]?.clock_domain ?? null;
}

export function replayAxis(events: readonly ContractEvent[], cursor: number): ReplayAxis {
  const domain = axisDomain(events);
  const held = Math.max(0, Math.min(cursor, events.length));
  const onAxis = events.filter((event) => event.clock_domain === domain);
  const first = onAxis[0] ?? null;
  const last = onAxis[onAxis.length - 1] ?? null;
  const foldedOnAxis = events.slice(0, held).filter((event) => event.clock_domain === domain);
  const atCursor = foldedOnAxis[foldedOnAxis.length - 1] ?? null;
  return {
    records: events.length,
    folded: held,
    clock_domain: domain,
    on_axis: onAxis.length,
    off_axis: events.length - onAxis.length,
    elapsed_ns: first === null || atCursor === null || onAxis.length < 2 ? null : atCursor.t_mono_ns - first.t_mono_ns,
    span_ns: first === null || last === null || onAxis.length < 2 ? null : last.t_mono_ns - first.t_mono_ns,
  };
}

/** How long one turn of the replay clock is. It advances the cursor by this much of the RECORDING's own time. */
export const REPLAY_TICK_MS = 250;

/**
 * Where the cursor goes on the next turn of the replay clock.
 *
 * It advances by the recording's own elapsed time, so a recording plays at the speed it happened at rather than at a
 * record per tick. Two cases refuse that and step forward exactly one record instead: a record whose clock is not the
 * axis clock, which has no place on the axis at all, and a cursor that has folded nothing yet, which has no reading
 * to measure from. Either way the cursor always moves, so play can never look like a control that did nothing.
 */
export function advanceReplayCursor(events: readonly ContractEvent[], cursor: number, tickMs: number = REPLAY_TICK_MS): number {
  if (cursor >= events.length) return events.length;
  const domain = axisDomain(events);
  const last = cursor > 0 ? (events[cursor - 1] ?? null) : null;
  if (last === null || last.clock_domain !== domain) return cursor + 1;
  const limit = last.t_mono_ns + tickMs * 1e6;
  let next = cursor;
  while (next < events.length) {
    const event = events[next] as ContractEvent;
    if (event.clock_domain !== domain || event.t_mono_ns > limit) break;
    next += 1;
  }
  return Math.max(next, cursor + 1);
}

/* ---- what happened between START and the end of the run ------------------------------------------------------ */

/**
 * How often the machine entered the same state again, counted from the transitions this fold holds.
 *
 * THIS IS NOT THE RETRY COUNT and the screen must never present it as one. The controller counts its own retries by
 * kind and reports their SUM in the run's own record; the per-kind figures and the per-attempt numbers it prints are
 * not in the record stream at all. What can be counted here is how often a state was entered again, which is an
 * observation of the transitions and nothing more.
 */
export interface StateReentry {
  state: string;
  /** Every entry into the state the fold holds, the first one included; it was entered AGAIN `entries - 1` times. */
  entries: number;
}

export function stateReentries(view: ViewState): StateReentry[] {
  const counts = new Map<string, number>();
  for (const step of view.machine?.history ?? []) {
    counts.set(step.state, (counts.get(step.state) ?? 0) + 1);
  }
  return [...counts.entries()]
    .filter(([, entries]) => entries > 1)
    .map(([state, entries]) => ({ state, entries }))
    .sort((a, b) => (b.entries === a.entries ? a.state.localeCompare(b.state) : b.entries - a.entries));
}

/**
 * The runtime's own reason for why an operator command count cannot be more than one while a run is in flight.
 *
 * It is read from the capability list the runtime serves, never written here: the rule belongs to the runtime and
 * this console states it only where the runtime has stated it first. Null when no capability is blocked for it, in
 * which case the screen says nothing about a rule it cannot quote.
 */
export function commandBlockReason(view: ViewState): string | null {
  const blocked = view.capabilities.find((capability) => capability.state === 'blocked' && capability.reason !== null);
  return blocked?.reason ?? null;
}
