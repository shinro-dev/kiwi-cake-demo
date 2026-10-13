// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The pure reducer. Live and replay use the SAME functions: live folds events as they arrive, replay folds
// events[0..cursor] from the beginning. Nothing here reads a clock, a network or the DOM.
//
// Two rules this file exists to enforce:
//
//  1. A gap is never silent. A `resync` line from the runtime, and an event whose seq skips ahead without one, both
//     land in `stream_gaps`.
//  2. The decision frame is pinned. It changes when a predicate or an action consumes DIFFERENT evidence, and at no
//     other time. A newly captured observation does not move it, so a result is never shown beside the frame that
//     came after it.
//  3. THE ONLY ORDERING THIS FOLD RELIES ON IS THE ENVELOPE'S seq, and that sequence space belongs to ONE runtime
//     event log. A record it has already folded is ignored rather than folded twice; a cursor the runtime says belongs
//     to another log restarts the fold instead of continuing it; and a source's own sequence number (a tap sample, a
//     preview frame, `consumed_source_stamp.seq`) is never compared with it, because the two count different things.

import type { PayloadRunFinished, Snapshot, SnapshotLastOutcome } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import { parseUnresolved } from './uncertain';
import type {
  ActionView,
  DecisionFrame,
  MeasuredStateView,
  ObservationMeta,
  OutcomeView,
  PerceptionRecord,
  PredicateView,
  SourceSessionView,
  StreamGap,
  TimingSpanView,
  ViewState,
} from './types';

const RECENT_EVENTS_MAX = 400;
const DIAGNOSTICS_MAX = 200;
const ACTION_HISTORY_MAX = 60;
const GAPS_MAX = 50;
const SOURCE_SESSIONS_MAX = 20;

/** The first seq of a runtime event log. `demo_runtime.journal.EventLog.append` pre-increments from zero. */
const FIRST_SEQ = 1;

const EMPTY_OUTCOME: OutcomeView = {
  reported: null,
  confirmed: null,
  predicates: null,
  exit_status: null,
  evidence_complete: null,
  evidence_gaps: null,
  // Not zero: nothing has counted them yet, and a zero would read as a measurement that came back empty.
  automatic_retries: null,
  task_control_interventions: null,
  run_id: null,
  finished_seq: null,
  reason_code: null,
  phase: null,
  failure: null,
  finished_wall_utc: null,
};

export function initialState(): ViewState {
  return {
    contract_compatible: true,
    commands_enabled: true,
    read_only_reason: null,
    snapshot_at_ms: null,
    runtime_session: null,
    seq_cursor: 0,
    retained_from_seq: 0,
    // NOT RECORDED until a snapshot says it. A default of synthetic drew a runtime that had not answered as a rehearsal.
    data_mode: null,
    link: { state: 'unknown', source_stamp: null, receive_age_ms: null },
    owner: { kind: 'unresolved', run_id: null, lease: null },
    lease: null,
    execution: null,
    active_run: null,
    unresolved: null,
    machine: null,
    current_action: null,
    action_history: [],
    decision_frame: null,
    predicates: [],
    observations: {},
    snapshot_observation: null,
    last_perception: null,
    perception_by_observation: {},
    measured_state: null,
    snapshot_measured_state: null,
    run_config: null,
    run_started: null,
    outcome: EMPTY_OUTCOME,
    previous_outcome: null,
    stop: { requested: null, acknowledged: null, outcome: null },
    evidence: { complete: true, gaps: 0, dropped_telemetry: 0 },
    evidence_gap_events: [],
    stream_gaps: [],
    dropped_telemetry_events: [],
    timings: [],
    runtime_timings: null,
    run_evidence: null,
    diagnostics: [],
    capabilities: [],
    components: [],
    cake: {
      synthetic: true,
      ladder: {
        synthetic: true,
        artifacts_available: { value: null, evidence: 'no snapshot has been read yet' },
        resident_reachable: { value: null, evidence: 'no snapshot has been read yet' },
        admin_interface_compatible: { value: null, evidence: 'no snapshot has been read yet' },
        modules_active: { value: null, evidence: 'no snapshot has been read yet' },
        mission_participation: { value: null, evidence: 'no snapshot has been read yet' },
      },
      slots: [],
      transactions: [],
      update_preview: null,
      continuity: null,
    },
    counters: {
      reconnects: 0,
      runtime_restarts: 0,
      malformed_lines: 0,
      drop_notices: 0,
      cursor_replacements: 0,
      stale_snapshots: 0,
      duplicate_records: 0,
      source_session_changes: 0,
    },
    last_event_seq: null,
    source_sessions: [],
    recent_events: [],
  };
}

/** The event-derived half of the state, cleared when a different runtime session answers. */
function clearedRunDetail(): Pick<
  ViewState,
  | 'machine'
  | 'current_action'
  | 'action_history'
  | 'decision_frame'
  | 'predicates'
  | 'observations'
  | 'last_perception'
  | 'perception_by_observation'
  | 'measured_state'
  | 'run_config'
  | 'run_started'
  | 'stop'
  | 'evidence_gap_events'
  | 'stream_gaps'
  | 'dropped_telemetry_events'
  | 'timings'
  | 'runtime_timings'
  | 'run_evidence'
  | 'diagnostics'
  | 'last_event_seq'
  | 'source_sessions'
  | 'recent_events'
> {
  const fresh = initialState();
  return {
    machine: fresh.machine,
    current_action: fresh.current_action,
    action_history: fresh.action_history,
    decision_frame: fresh.decision_frame,
    predicates: fresh.predicates,
    observations: fresh.observations,
    last_perception: fresh.last_perception,
    perception_by_observation: fresh.perception_by_observation,
    measured_state: fresh.measured_state,
    run_config: fresh.run_config,
    run_started: fresh.run_started,
    stop: fresh.stop,
    evidence_gap_events: fresh.evidence_gap_events,
    stream_gaps: fresh.stream_gaps,
    dropped_telemetry_events: fresh.dropped_telemetry_events,
    timings: fresh.timings,
    runtime_timings: fresh.runtime_timings,
    run_evidence: fresh.run_evidence,
    diagnostics: fresh.diagnostics,
    last_event_seq: fresh.last_event_seq,
    source_sessions: fresh.source_sessions,
    recent_events: fresh.recent_events,
  };
}

function outcomeFrom(
  result: PayloadRunFinished,
  runId: string | null,
  finishedSeq: number | null,
  finishedWallUtc: string | null = null,
): OutcomeView {
  const p = result.predicates;
  const allFour =
    p.object_secured === true && p.placed === true && p.clearance_target_met === true && p.base_returned === true;
  return {
    reported: result.outcome,
    confirmed: result.outcome === 'placed_new' && allFour ? 'placed_new' : null,
    predicates: p,
    exit_status: result.exit_status,
    evidence_complete: result.evidence_complete,
    evidence_gaps: result.evidence_gaps,
    automatic_retries: result.automatic_retries,
    task_control_interventions: result.task_control_interventions,
    run_id: runId,
    finished_seq: finishedSeq,
    // Optional on the contract, because a record written before the field existed carries none. `?? null` keeps
    // "absent" and "explicitly null" as the same fact, which they are: the record does not say.
    reason_code: result.reason_code ?? null,
    phase: result.phase ?? null,
    failure: result.failure ?? null,
    finished_wall_utc: finishedWallUtc,
  };
}

/**
 * Whether a carried outcome finished LATER in the runtime's log than the one already held.
 *
 * Both sequence numbers count in one ordered log of one runtime session, which is the only thing that makes them
 * comparable at all; a fold holding no outcome yet has nothing to be later than, so anything is. An outcome for the
 * run already held is not later than itself and changes nothing.
 */
function outcomeIsLater(carried: OutcomeView, held: OutcomeView, started: ViewState['run_started']): boolean {
  // A RUN THIS FOLD STARTED AND HAS NOT SEEN END owns the outcome slot: only its own end may fill it. The snapshot's
  // `last_outcome` is the newest FINISHED run, which is the one before it until this one ends, and taking it here drew a
  // finished earlier run as the end of the run in progress.
  if (held.run_id === null && started !== null && typeof started.run_id === 'string') return carried.run_id === started.run_id;
  if (held.run_id === null) return true;
  if (carried.run_id === held.run_id) return false;
  if (held.finished_seq === null) return true;
  return carried.finished_seq !== null && carried.finished_seq > held.finished_seq;
}

/**
 * The snapshot's `latest_retained_observation`, read into the same shape a folded capture has.
 *
 * It is read as an OPTIONAL property, because a runtime that predates it sends none and an absence there is that
 * runtime saying nothing rather than a runtime saying it kept no frame. The fields map one to one onto the folded
 * record, which is the point of the contract carrying exactly the ones `observation.captured` carries: the camera card
 * then draws one shape and never has to decide which of two descriptions of the same capture to believe.
 */
function fromSnapshotObservation(value: Snapshot['latest_retained_observation']): ObservationMeta | null {
  if (value === null || value === undefined) return null;
  return {
    observation_id: value.observation_id,
    source_role: value.source_role,
    media: value.media,
    width_px: value.width_px,
    height_px: value.height_px,
    content_digest: value.content_digest,
    source_stamp: value.source_stamp,
    retained: value.retained,
    seq: value.seq,
    t_wall_utc: value.t_wall_utc,
  };
}

/**
 * The snapshot's `latest_measured_state`, read into the same shape a folded `measured.state` record has.
 *
 * Optional for the same reason the frame reference is: a runtime that predates the property sends none, and an absence
 * there is that runtime saying nothing rather than a runtime saying no controller ever read the robot. The fields map
 * one to one onto the folded record, which is what lets the two be decided between by SEQUENCE NUMBER instead of by
 * which description of the same reading a panel happens to trust.
 */
function fromSnapshotMeasuredState(value: Snapshot['latest_measured_state']): MeasuredStateView | null {
  if (value === null || value === undefined) return null;
  return {
    joints: { ...value.joints },
    base_velocity: value.base_velocity,
    consumed_source_stamp: value.consumed_source_stamp,
    seq: value.seq,
    t_wall_utc: value.t_wall_utc,
  };
}

function fromLastOutcome(last: SnapshotLastOutcome | null): OutcomeView | null {
  if (last === null) return null;
  return outcomeFrom(last.result, last.run_id, last.finished_seq, last.finished_wall_utc ?? null);
}

/**
 * Fold an authoritative snapshot. `data_mode` and `link` are copied SEPARATELY and neither is derived from the other:
 * a live view whose link dropped stays live.
 *
 * `unresolved_command` and `lease` are now OPTIONAL PROPERTIES of the contract snapshot rather than additive keys
 * beside it, so they are read off the object itself and no cast is needed to reach them. They are still read
 * defensively: absence is the runtime saying there is no block, and a present object this console cannot describe is
 * still a block, which is `parseUnresolved`'s rule and not this one.
 *
 * A LATE SNAPSHOT NEVER OVERWRITES NEWER EVENTS. A snapshot is a photograph of the runtime at ITS `seq_cursor`, and
 * the 5 s poll races the event stream, so one can arrive after records the stream already delivered. Everything in it
 * was computed BEFORE those records, so when its cursor is behind the fold nothing of it is applied at all. This is
 * the rule this function already applied to `last_outcome` alone, generalized to every field a later event can move:
 * the link, the owner, the lease, the run in progress, the block, the capability and component lists, the Cake view
 * and the evidence counters.
 *
 * `atMs` is WHEN THIS SNAPSHOT REACHED THIS BROWSER, on this computer's clock, and it is the one reading of a clock
 * this fold takes. It is not a fact from the record and it is never treated as one: it is stamped here because this
 * is the one place that knows a snapshot was applied, and it is what lets a screen say that the capability list it is
 * drawing arrived four minutes ago. A caller that wants the fold to be pure passes the stamp in, which is what the
 * tests do; the store takes the default because the moment this runs IS the moment the answer arrived. A snapshot
 * that is refused as late above never reaches here, so the stamp only ever moves for a snapshot that was applied.
 */
export function applySnapshot(state: ViewState, snapshot: Snapshot, atMs: number = Date.now()): ViewState {
  // Until a snapshot has named the session, this fold does not know WHOSE log its cursor counts in, so there is
  // nothing to call a snapshot late against: the first snapshot is always applied in full.
  const known = state.runtime_session !== null;
  const restarted = known && state.runtime_session !== snapshot.runtime_session;
  const base: ViewState = restarted ? { ...state, ...clearedRunDetail() } : state;
  // A restart is not staleness: the new session's cursor counts from its own log and is unrelated to the old one's.
  if (known && !restarted && snapshot.seq_cursor < base.seq_cursor) {
    return { ...base, counters: { ...base.counters, stale_snapshots: base.counters.stale_snapshots + 1 } };
  }
  const carriedOutcome = fromLastOutcome(snapshot.last_outcome);
  return {
    ...base,
    snapshot_at_ms: atMs,
    runtime_session: snapshot.runtime_session,
    seq_cursor: restarted ? snapshot.seq_cursor : Math.max(base.seq_cursor, snapshot.seq_cursor),
    retained_from_seq: snapshot.retained_from_seq,
    data_mode: snapshot.data_mode,
    link: snapshot.link,
    owner: snapshot.owner,
    lease: snapshot.lease ?? null,
    // The backend and the ownership under it, exactly as the runtime stated them. A runtime that sends nothing here
    // has said nothing about either, which the screen prints as a question nothing has answered rather than filling
    // it in from the lease beside it: a runtime on the baseline backend holds no lease at all, on purpose, and
    // reading an absent lease as an absence of ownership is the mistake this field exists to prevent.
    execution: snapshot.execution ?? null,
    active_run: snapshot.active_run,
    unresolved: parseUnresolved(snapshot.unresolved_command ?? null),
    // The newest retained frame the RUNTIME knows about, which is what a tab that connected or reloaded after the
    // capture has instead of a fold. It is written here on every applied snapshot and it never touches `observations`:
    // the fold is the fresher of the two and stays authoritative wherever it holds anything at all.
    snapshot_observation: fromSnapshotObservation(snapshot.latest_retained_observation),
    // The newest measurement the RUNTIME knows about, kept beside the folded one and never merged into it here. The
    // two are decided between where they are read, by the sequence number both carry, and in both directions: a
    // snapshot in flight can be older than what has been folded since, and a record folded minutes ago can be older
    // than the snapshot arriving now. See `model/merge.ts` and `model/measurement.ts`.
    snapshot_measured_state: fromSnapshotMeasuredState(snapshot.latest_measured_state),
    capabilities: snapshot.capabilities,
    components: snapshot.components,
    cake: snapshot.cake,
    evidence: snapshot.evidence,
    // What the RUNTIME aggregated, carried as it arrived and never merged with the fold above it: the two count
    // different windows, and a runtime that aggregates none says so by sending none, which is not a run with no
    // spans and not a run with no evidence.
    runtime_timings: snapshot.timings ?? null,
    run_evidence: snapshot.run_evidence ?? null,
    // A SNAPSHOT'S last_outcome IS THE PREVIOUS RUN, and it is kept as that and nothing else.
    //
    // The runtime says so itself: `last_outcome` is the most recent FINISHED run, which is the previous one whenever
    // another is active. It used to be folded into `outcome` whenever its run id differed from the one held here,
    // which is wrong in the one case that costs the most: a run finishes badly, a snapshot built a moment earlier
    // arrives with the SUCCESS of the run before it, its run id differs, and the failure on the screen is replaced by
    // a success that belongs to another run. The poll races the stream, so this is an ordinary race and not a rare
    // one.
    //
    // The rule now is monotonic in the runtime's own ordering: a carried outcome is taken only when it finished LATER
    // in the log than the one already folded here, which is what makes it impossible for an older result to replace a
    // newer one. A fresh tab holds none, so the first snapshot's outcome is taken whole, which is what a reload needs.
    // A replaced runtime counts in another log altogether, so its sequence numbers are not comparable with the old
    // one's and the carried outcome is taken as it stands.
    outcome: carriedOutcome !== null && (restarted || outcomeIsLater(carriedOutcome, base.outcome, base.run_started)) ? carriedOutcome : base.outcome,
    // Kept apart from `outcome` on purpose. It is what lets the screen say "previous run" with the time it finished,
    // instead of letting it stand in for the run being looked at now.
    previous_outcome: carriedOutcome ?? (restarted ? null : base.previous_outcome),
    counters: { ...base.counters, runtime_restarts: base.counters.runtime_restarts + (restarted ? 1 : 0) },
  };
}

export function markReconnect(state: ViewState): ViewState {
  return { ...state, counters: { ...state.counters, reconnects: state.counters.reconnects + 1 } };
}

/** The server told this reader it dropped records for it. A notice, counted; never mistaken for a parse failure. */
export function markDropNotice(state: ViewState): ViewState {
  return {
    ...state,
    counters: { ...state.counters, drop_notices: state.counters.drop_notices + 1 },
    evidence: { ...state.evidence, complete: false },
  };
}

export function markMalformedLine(state: ViewState): ViewState {
  return { ...state, counters: { ...state.counters, malformed_lines: state.counters.malformed_lines + 1 } };
}

/** An incompatible `hello`. The tab becomes read-only and says why; it does not guess at another version. */
export function markContractIncompatible(state: ViewState, reasonCode: string): ViewState {
  return { ...state, contract_compatible: false, commands_enabled: false, read_only_reason: reasonCode };
}

/**
 * The server says this tab may not command, but the contract is fine. The record keeps arriving and keeps being shown;
 * the controls go away with the server's reason. This is NOT the same state as an incompatible contract and the screen
 * does not say that it is.
 */
export function markCommandsDisabled(state: ViewState, reasonCode: string | null): ViewState {
  return { ...state, commands_enabled: false, read_only_reason: reasonCode ?? 'read_only' };
}

function pushGap(gaps: StreamGap[], gap: StreamGap): StreamGap[] {
  if (gap.to_seq < gap.from_seq) return gaps;
  return [...gaps, gap].slice(-GAPS_MAX);
}

/**
 * The runtime announced that it cannot serve from the cursor asked for. The missing range is recorded, not skipped.
 *
 * There are TWO of these lines and they mean different things. With a `missed` RANGE the runtime is serving the same
 * log and has dropped records this reader had not read yet: the range is recorded as a declared gap and the fold goes
 * on. With `missed` NULL (the runtime's `cursor_ahead`) the cursor is ahead of the log, which happens when it belongs
 * to a log the runtime is not serving any more: a REPLACED RUNTIME SESSION. There is no missing range to state then,
 * because the two cursors count different things, and going on folding would be reading a new log's records against
 * the old log's sequence. The fold is restarted from the new log instead: the run detail is cleared exactly as a
 * changed `runtime_session` in a snapshot clears it, the cursor is moved back to what the runtime can serve, and
 * everything the new log wrote before that is recorded as a declared gap, because this tab never saw it.
 */
export function applyResync(
  state: ViewState,
  resync: { retained_from_seq: number; missed: [number, number] | null; reason?: string | null },
): ViewState {
  if (resync.missed === null) {
    const cleared: ViewState = { ...state, ...clearedRunDetail() };
    return {
      ...cleared,
      // The session this fold belonged to is over and the next snapshot is what names the one it belongs to now.
      // Holding the old name here would make that snapshot look like a restart and clear the new log's records a
      // second time, and those records are this session's: they arrived after the runtime said the cursor was.
      runtime_session: null,
      retained_from_seq: resync.retained_from_seq,
      seq_cursor: Math.max(0, resync.retained_from_seq - 1),
      stream_gaps: pushGap(cleared.stream_gaps, { from_seq: FIRST_SEQ, to_seq: resync.retained_from_seq - 1, declared: true }),
      counters: { ...cleared.counters, cursor_replacements: cleared.counters.cursor_replacements + 1 },
    };
  }
  const [from, to] = resync.missed;
  return {
    ...state,
    retained_from_seq: resync.retained_from_seq,
    stream_gaps: pushGap(state.stream_gaps, { from_seq: from, to_seq: to, declared: true }),
  };
}

/** True when a gap already on record covers this range, so an announced gap is not counted a second time. */
function alreadyCovered(gaps: readonly StreamGap[], from: number, to: number): boolean {
  return gaps.some((gap) => gap.from_seq <= from && gap.to_seq >= to);
}

function sameIds(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false;
  return true;
}

function unique(ids: readonly string[]): string[] {
  const out: string[] = [];
  for (const id of ids) if (!out.includes(id)) out.push(id);
  return out;
}

/**
 * What this record says it consumed.
 *
 * A record that NAMES its evidence is taken at its word and provenance is not consulted for it: the two must never
 * both contribute to one id list, or the pinned frame would thrash between the same decision's two descriptions.
 * `evidence_refs` is the optional extension that lets a decision and a refusal state what they looked at on the
 * record itself; provenance is the fallback, and it is not a statement about what was looked at, because it also
 * carries model and calibration identity.
 *
 * A refusal pins the frame ONLY when it names its evidence. Every refusal recorded before that field existed names
 * none, so it moves nothing, exactly as it did before.
 */
function consumedObservationIds(event: ContractEvent): string[] {
  if (event.kind === 'predicate.evaluated') {
    const ids: string[] = [];
    for (const piece of event.payload.evidence) for (const id of piece.observation_ids) if (!ids.includes(id)) ids.push(id);
    return ids;
  }
  if (event.kind === 'action.planned') {
    const named = event.payload.evidence_refs;
    if (named !== undefined) return unique(named);
    return event.provenance !== null ? [...event.provenance.observation_ids] : [];
  }
  if (event.kind === 'action.refused') {
    const named = event.payload.evidence_refs;
    return named === undefined ? [] : unique(named);
  }
  if (event.kind === 'action.started') {
    return event.provenance !== null ? [...event.provenance.observation_ids] : [];
  }
  return [];
}

/**
 * Which producer sessions this fold contains.
 *
 * A record's `source_session` is the session of whatever PRODUCED it, and that is not one thing: the daemon's records
 * carry the runtime session, while the controller's carry a controller session minted per run. So a session that does
 * not match the runtime's is normal and must not be refused, or every controller record would be thrown away. What is
 * worth stating is which producer sessions a fold actually contains, so a reader can see that a run's records came
 * from the producer it thinks they did, and a change of session for one producer is counted rather than passed over.
 */
function noteSourceSession(list: readonly SourceSessionView[], event: ContractEvent): { sessions: SourceSessionView[]; changed: boolean } {
  const producer = event.producer.name;
  const session = event.source_session;
  const at = list.findIndex((item) => item.producer === producer && item.session === session);
  if (at >= 0) {
    const copy = list.slice();
    const prev = copy[at] as SourceSessionView;
    copy[at] = { ...prev, last_seq: Math.max(prev.last_seq, event.seq), records: prev.records + 1 };
    return { sessions: copy, changed: false };
  }
  const changed = list.some((item) => item.producer === producer);
  return {
    sessions: [...list, { producer, session, first_seq: event.seq, last_seq: event.seq, records: 1 }].slice(-SOURCE_SESSIONS_MAX),
    changed,
  };
}

/**
 * ONLY A RETAINED PICTURE IS PINNED (verdict 2026-09-27 item 10). A decision can consume a reading with no picture (the
 * base velocity, media scalar_set) or a frame the runtime did not keep; neither is a stored frame, and pinning one drew an
 * <img> for a picture that does not exist. An id whose capture record has not arrived is pinned as before, and the
 * capture binds to it when it does (the Evidence card then says what it is).
 */
function pinnablePicture(state: ViewState, id: string): boolean {
  const capture = state.observations[id];
  return capture === undefined || (capture.media !== 'scalar_set' && capture.retained);
}

function pinFrame(state: ViewState, event: ContractEvent, consumed: string[]): DecisionFrame | null {
  const ids = consumed.filter((id) => pinnablePicture(state, id));
  if (ids.length === 0) return state.decision_frame;
  const by: DecisionFrame['consumed_by'] =
    event.kind === 'predicate.evaluated'
      ? { by: 'predicate', name: event.payload.predicate, seq: event.seq }
      : {
          by: 'action',
          name:
            event.kind === 'action.planned' || event.kind === 'action.started' || event.kind === 'action.refused'
              ? event.payload.action_type
              : event.kind,
          seq: event.seq,
        };

  const current = state.decision_frame;
  if (current !== null && sameIds(current.observation_ids, ids)) {
    // The same evidence was consumed again. The frame stays exactly where it was pinned.
    return { ...current, consumed_by: by };
  }
  const primary = ids[0] ?? null;
  const perception: PerceptionRecord | null = primary !== null ? state.perception_by_observation[primary] ?? null : null;
  const capture: ObservationMeta | null = primary !== null ? state.observations[primary] ?? null : null;
  return {
    observation_ids: ids,
    primary_observation_id: primary,
    consumed_by: by,
    perception,
    capture,
    pinned_at_seq: event.seq,
  };
}

/** The scene scan's fused results (`scene-view`, `scene-block`, `scene-pad`), which describe several frames at once. */
export function isSceneResult(record: PerceptionRecord): boolean {
  return record.payload.detector.startsWith('scene-');
}

function upsertPredicate(list: PredicateView[], next: PredicateView): PredicateView[] {
  const at = list.findIndex((item) => item.predicate === next.predicate);
  if (at < 0) return [...list, next];
  const copy = list.slice();
  copy[at] = next;
  return copy;
}

/**
 * Add one measured duration to the aggregate.
 *
 * A ROW IS A SPAN IN ONE CLOCK DOMAIN, and that is why the key is the pair. The controller emits the span `capture`
 * in the laptop's monotonic clock and also in the robot computer's, and this used to find the row by name alone, add
 * the second duration into the first row's total and maximum, and then set the domain to the word unknown because the
 * two did not agree. What came out was a sum across two clocks carrying a label saying nobody knew which, under a
 * panel that promises a duration is only ever compared with another from the same clock. They are two rows now.
 *
 * The window is kept beside the count for the same kind of reason: a total over four samples taken between two
 * sequence numbers is a different statement from the same total over forty, and the number alone does not say which.
 */
function upsertTiming(list: TimingSpanView[], span: string, durationNs: number, clockDomain: string, seq: number): TimingSpanView[] {
  const at = list.findIndex((item) => item.span === span && item.clock_domain === clockDomain);
  if (at < 0) {
    return [
      ...list,
      { span, count: 1, total_ns: durationNs, last_ns: durationNs, max_ns: durationNs, clock_domain: clockDomain, first_seq: seq, last_seq: seq },
    ];
  }
  const copy = list.slice();
  const prev = copy[at] as TimingSpanView;
  copy[at] = {
    span,
    count: prev.count + 1,
    total_ns: prev.total_ns + durationNs,
    last_ns: durationNs,
    max_ns: Math.max(prev.max_ns, durationNs),
    clock_domain: prev.clock_domain,
    first_seq: Math.min(prev.first_seq, seq),
    last_seq: Math.max(prev.last_seq, seq),
  };
  return copy;
}

/**
 * Fold one contract event.
 *
 * A RECORD THIS FOLD HAS ALREADY SEEN IS COUNTED AND IGNORED. The stream can deliver one twice: a reconnect resumes
 * from the cursor already reached, and the runtime serves the backlog from there. Folding it a second time would
 * re-increment the evidence gaps and the dropped-telemetry count, append it to the recent records again and re-pin the
 * decision frame, all of which say that something happened twice when it happened once. The guard compares against
 * `last_event_seq` and not against `seq_cursor`, because a snapshot moves the cursor and a replay folds from the
 * beginning of a recording.
 */
export function applyEvent(state: ViewState, event: ContractEvent): ViewState {
  if (state.last_event_seq !== null && event.seq <= state.last_event_seq) {
    return { ...state, counters: { ...state.counters, duplicate_records: state.counters.duplicate_records + 1 } };
  }

  const skipped: StreamGap[] =
    state.last_event_seq !== null &&
    event.seq > state.last_event_seq + 1 &&
    !alreadyCovered(state.stream_gaps, state.last_event_seq + 1, event.seq - 1)
      ? pushGap(state.stream_gaps, { from_seq: state.last_event_seq + 1, to_seq: event.seq - 1, declared: false })
      : state.stream_gaps;
  const sources = noteSourceSession(state.source_sessions, event);

  let next: ViewState = {
    ...state,
    stream_gaps: skipped,
    last_event_seq: state.last_event_seq === null ? event.seq : Math.max(state.last_event_seq, event.seq),
    seq_cursor: Math.max(state.seq_cursor, event.seq),
    source_sessions: sources.sessions,
    counters: sources.changed
      ? { ...state.counters, source_session_changes: state.counters.source_session_changes + 1 }
      : state.counters,
    recent_events: [...state.recent_events, event].slice(-RECENT_EVENTS_MAX),
  };

  switch (event.kind) {
    case 'run.started': {
      next = {
        ...next,
        ...clearedRunDetail(),
        stream_gaps: next.stream_gaps,
        last_event_seq: next.last_event_seq,
        // The producer sessions of this fold outlive a run boundary: the record that started the run is one of them.
        source_sessions: next.source_sessions,
        recent_events: next.recent_events,
        run_started: {
          command_id: event.payload.command_id,
          command_type: event.payload.command_type,
          plan_only: event.payload.plan_only,
          holding: event.payload.holding,
          // The envelope's own wall time, which is the one clock reading that dates the start of this run. It is kept
          // exactly as the runtime wrote it and is never replaced by the moment this tab folded the record.
          started_wall_utc: event.t_wall_utc,
          // Which run this is, so that no outcome of another run is ever taken for its own (`outcomeIsLater`).
          run_id: event.run_id,
          seq: event.seq,
        },
        outcome: EMPTY_OUTCOME,
      };
      break;
    }
    case 'run.config':
      next = { ...next, run_config: { config_digest: event.payload.config_digest, summary: event.payload.summary } };
      break;
    case 'run.finished':
      // THE ENVELOPE'S OWN WALL TIME, kept for the same reason `run.started` keeps its own: it is the one clock
      // reading that dates the END of this run, and both readings are the RUNTIME's, so the duration between them is
      // a subtraction inside a single clock. It used to be dropped here, which left `missionTime` with a start and no
      // finish for the ordinary case, and a run this tab watched from beginning to end went on counting up after it
      // had ended. A run read back from the runtime's finished-run list already carried its finish, so the tab that
      // saw the least was the only one that could state the duration. That was the wrong way round.
      next = { ...next, outcome: outcomeFrom(event.payload, event.run_id, event.seq, event.t_wall_utc) };
      break;
    case 'state.transition': {
      const history = [...(next.machine?.history ?? []), { state: event.payload.to_state, seq: event.seq }].slice(-ACTION_HISTORY_MAX);
      next = {
        ...next,
        machine: {
          machine: event.payload.machine,
          state: event.payload.to_state,
          from_state: event.payload.from_state,
          since_seq: event.seq,
          history,
        },
      };
      break;
    }
    case 'action.planned': {
      const action: ActionView = {
        action_id: event.action_id,
        action_type: event.payload.action_type,
        plan_only: event.payload.plan_only,
        parameters: event.payload.parameters,
        phase: 'planned',
        result: null,
        duration_ns: null,
        refusal: null,
        seq: event.seq,
      };
      next = {
        ...next,
        current_action: action,
        action_history: [...next.action_history, action].slice(-ACTION_HISTORY_MAX),
        decision_frame: pinFrame(next, event, consumedObservationIds(event)),
      };
      break;
    }
    case 'action.started': {
      const current = next.current_action;
      const action: ActionView =
        current !== null && current.action_id === event.action_id
          ? { ...current, phase: 'started' }
          : {
              action_id: event.action_id,
              action_type: event.payload.action_type,
              plan_only: false,
              parameters: [],
              phase: 'started',
              result: null,
              duration_ns: null,
              refusal: null,
              seq: event.seq,
            };
      next = { ...next, current_action: action, decision_frame: pinFrame(next, event, consumedObservationIds(event)) };
      break;
    }
    case 'action.completed': {
      const current = next.current_action;
      const action: ActionView =
        current !== null && current.action_id === event.action_id
          ? { ...current, phase: 'completed', result: event.payload.result, duration_ns: event.payload.duration_ns }
          : {
              action_id: event.action_id,
              action_type: event.payload.action_type,
              plan_only: false,
              parameters: [],
              phase: 'completed',
              result: event.payload.result,
              duration_ns: event.payload.duration_ns,
              refusal: null,
              seq: event.seq,
            };
      next = { ...next, current_action: action, action_history: [...next.action_history.slice(0, -1), action].slice(-ACTION_HISTORY_MAX) };
      break;
    }
    case 'action.refused': {
      const action: ActionView = {
        action_id: event.action_id,
        action_type: event.payload.action_type,
        plan_only: false,
        parameters: [],
        phase: 'refused',
        result: null,
        duration_ns: null,
        refusal: { reason_code: event.reason_code, detail: event.payload.detail, before_motion: event.payload.before_motion },
        seq: event.seq,
      };
      next = {
        ...next,
        current_action: action,
        action_history: [...next.action_history, action].slice(-ACTION_HISTORY_MAX),
        // A refusal is a decision, and the frame it was taken on is the one an operator needs to see. It moves the
        // pin only when the record NAMES the evidence: every refusal written before `evidence_refs` existed names
        // none, consumes nothing here and leaves the frame exactly where the last decision pinned it.
        decision_frame: pinFrame(next, event, consumedObservationIds(event)),
      };
      break;
    }
    case 'measured.state':
      next = {
        ...next,
        measured_state: {
          joints: { ...event.payload.joints },
          base_velocity: event.payload.base_velocity,
          consumed_source_stamp: event.payload.consumed_source_stamp,
          seq: event.seq,
          // The wall time the RUNTIME WROTE THE RECORD AT, carried unaltered. It is what lets the screen state the age
          // of this measurement from a real timestamp instead of counting records, and it is the RECORDING time: the
          // instant the servo bus was sampled is another clock and nothing here relates the two.
          t_wall_utc: event.t_wall_utc,
        },
      };
      break;
    case 'observation.captured': {
      const id = event.observation_id;
      if (id !== null) {
        const meta: ObservationMeta = {
          observation_id: id,
          source_role: event.payload.source_role,
          media: event.payload.media,
          width_px: event.payload.width_px,
          height_px: event.payload.height_px,
          content_digest: event.payload.content_digest,
          source_stamp: event.payload.source_stamp,
          retained: event.payload.retained,
          seq: event.seq,
          t_wall_utc: event.t_wall_utc,
        };
        // Note what was captured; do NOT move the decision frame. A capture is not a decision.
        //
        // The one thing a capture DOES do is bind the frame already pinned on it to the picture the decision was
        // taken on: the record carries the content digest and the pixel size, and a frame pinned before its capture
        // record arrived would otherwise carry neither and could be drawn over a rendition of something else. Only
        // the frame whose own primary observation this is, and never any other.
        const frame = next.decision_frame;
        const bound = frame !== null && frame.primary_observation_id === id ? { ...frame, capture: meta } : frame;
        next = { ...next, observations: { ...next.observations, [id]: meta }, decision_frame: bound };
      }
      break;
    }
    case 'perception.result': {
      const record: PerceptionRecord = { payload: event.payload, provenance: event.provenance, seq: event.seq };
      // A SCAN'S FUSED RESULT IS NOT A FRAME'S OWN PERCEPTION. It names every view it was built from, and it arrives
      // after those views' own detections; letting it replace them drew the decision frame under the scene's summary.
      // It is filed only where nothing of the frame's own is held, and it never stands over a per-frame result.
      const keeps = (held: PerceptionRecord | null | undefined): boolean => held != null && isSceneResult(record) && !isSceneResult(held);
      const byObservation = { ...next.perception_by_observation };
      for (const id of event.payload.observation_ids) if (!keeps(byObservation[id])) byObservation[id] = record;
      let frame = next.decision_frame;
      if (
        frame !== null &&
        frame.primary_observation_id !== null &&
        event.payload.observation_ids.includes(frame.primary_observation_id) &&
        !keeps(frame.perception)
      ) {
        // The perception for the frame already on screen arrived after the pin: attach it, keep the pin.
        frame = { ...frame, perception: record };
      }
      const latest = keeps(next.last_perception) ? next.last_perception : record;
      next = { ...next, last_perception: latest, perception_by_observation: byObservation, decision_frame: frame };
      break;
    }
    case 'predicate.evaluated': {
      const view: PredicateView = {
        predicate: event.payload.predicate,
        value: event.payload.value,
        // Optional on the contract. A record that carries none keeps the older reading, which `predicateState` states
        // in one place: a value is an evaluation, and a null value is a predicate that was not evaluated. Null here
        // means the record did not say, which is not the same as any of the four states.
        state: event.payload.state ?? null,
        // The payload's OWN reason, which the contract requires when the state is not_applicable or unavailable. The
        // envelope's reason_code is a different thing and is what a console routes on; the two are not merged here.
        reason_code: event.payload.reason_code ?? null,
        rule_ref: event.payload.rule_ref ?? null,
        rule_digest: event.payload.rule_digest ?? null,
        threshold: event.payload.threshold,
        measured: event.payload.measured,
        unit: event.payload.unit ?? null,
        evidence: event.payload.evidence,
        consumed_source_stamp: event.payload.consumed_source_stamp,
        // Optional on the contract and absent when empty, so an older record folds to an empty list rather than to a
        // list of zeroes. This is what carries the values behind a refusal the operator has to be able to read.
        measurements: event.payload.measurements ?? [],
        seq: event.seq,
        t_mono_ns: event.t_mono_ns,
        clock_domain: event.clock_domain,
      };
      next = {
        ...next,
        predicates: upsertPredicate(next.predicates, view),
        decision_frame: pinFrame(next, event, consumedObservationIds(event)),
      };
      break;
    }
    case 'stop.requested':
      next = {
        ...next,
        stop: {
          requested: { origin: event.payload.origin, command_id: event.payload.command_id, seq: event.seq, t_mono_ns: event.t_mono_ns, t_wall_utc: event.t_wall_utc },
          acknowledged: null,
          outcome: null,
        },
      };
      break;
    case 'stop.acknowledged':
      next = {
        ...next,
        stop: {
          ...next.stop,
          acknowledged: { acknowledged_by: event.payload.acknowledged_by, latency_ns: event.payload.latency_ns, seq: event.seq, t_wall_utc: event.t_wall_utc },
        },
      };
      break;
    case 'stop.outcome':
      next = {
        ...next,
        stop: {
          ...next.stop,
          outcome: {
            outcome: event.payload.outcome,
            motion_ceased: event.payload.motion_ceased,
            evidence: event.payload.evidence,
            seq: event.seq,
            t_wall_utc: event.t_wall_utc,
            reason_code: event.reason_code,
          },
        },
      };
      break;
    case 'timing.span':
      next = { ...next, timings: upsertTiming(next.timings, event.payload.span, event.payload.duration_ns, event.payload.clock_domain, event.seq) };
      break;
    case 'evidence.gap':
      next = {
        ...next,
        evidence_gap_events: [
          ...next.evidence_gap_events,
          { gap: event.payload.gap, detail: event.payload.detail, observation_ids: event.payload.observation_ids, seq: event.seq },
        ].slice(-GAPS_MAX),
        evidence: { ...next.evidence, complete: false, gaps: next.evidence.gaps + 1 },
      };
      break;
    case 'telemetry.dropped':
      next = {
        ...next,
        dropped_telemetry_events: [
          ...next.dropped_telemetry_events,
          { count: event.payload.count, stage: event.payload.stage, first_seq: event.payload.first_seq, last_seq: event.payload.last_seq, seq: event.seq },
        ].slice(-GAPS_MAX),
        evidence: { ...next.evidence, dropped_telemetry: next.evidence.dropped_telemetry + event.payload.count },
      };
      break;
    case 'diagnostic.text':
      next = {
        ...next,
        diagnostics: [
          ...next.diagnostics,
          {
            seq: event.seq,
            stream: event.payload.stream,
            level: event.payload.level,
            text: event.payload.text,
            redacted: event.payload.redacted,
            reason_code: event.reason_code,
          },
        ].slice(-DIAGNOSTICS_MAX),
      };
      break;
    default:
      break;
  }
  return next;
}

/** Replay: the same reducer, over a prefix of a recorded run. */
export function foldEvents(state: ViewState, events: readonly ContractEvent[]): ViewState {
  let next = state;
  for (const event of events) next = applyEvent(next, event);
  return next;
}

/**
 * THE RECORDS OF A RUN IN PROGRESS, READ BACK AND MERGED INTO THE LIVE FOLD BY SEQ (redesign verdict item 5).
 *
 * A tab opened while a run is in progress (a reload, a second display) subscribes from the snapshot's cursor, so the
 * stream never delivers that run's earlier records: its start, its states, its decisions. The runtime serves every
 * record of the run from its log. This folds those together with what the stream has delivered since, in seq order and
 * from the run's own start, which is what a tab that watched the run from its start folded. The run's detail (its
 * states, actions, predicates, frames, measurements, stop, outcome) is rebuilt from those records alone; what the
 * snapshot supplied, the counters, the declared gaps and the producer sessions this fold holds are kept as they are, so
 * reading back states no gap, no duplicate and no restart the stream did not report.
 *
 * The fold is returned unchanged when the records carry no `run.started`, when this fold already folded that start (it
 * watched the run), or when it has started a later run since (the read came back after that run ended).
 */
export function mergeRunRecords(state: ViewState, events: readonly ContractEvent[]): ViewState {
  const start = events.find((event) => event.kind === 'run.started');
  if (start === undefined || start.run_id === null) return state;
  const held = state.run_started?.seq ?? null;
  if (held !== null && held >= start.seq) return state;
  const merged = new Map<number, ContractEvent>();
  for (const event of events) merged.set(event.seq, event);
  for (const event of state.recent_events) if (event.seq >= start.seq) merged.set(event.seq, event);
  const ordered = [...merged.values()].sort((a, b) => a.seq - b.seq);
  const run = foldEvents(initialState(), ordered);
  const last = ordered[ordered.length - 1]?.seq ?? null;
  return {
    ...state,
    machine: run.machine,
    current_action: run.current_action,
    action_history: run.action_history,
    decision_frame: run.decision_frame,
    predicates: run.predicates,
    observations: run.observations,
    last_perception: run.last_perception,
    perception_by_observation: run.perception_by_observation,
    measured_state: run.measured_state,
    run_config: run.run_config,
    run_started: run.run_started,
    stop: run.stop,
    evidence_gap_events: run.evidence_gap_events,
    dropped_telemetry_events: run.dropped_telemetry_events,
    timings: run.timings,
    diagnostics: run.diagnostics,
    outcome: run.outcome,
    recent_events: [...state.recent_events.filter((event) => event.seq < start.seq), ...ordered].slice(-RECENT_EVENTS_MAX),
    last_event_seq: last === null ? state.last_event_seq : Math.max(state.last_event_seq ?? last, last),
    seq_cursor: last === null ? state.seq_cursor : Math.max(state.seq_cursor, last),
  };
}
