// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The view state the reducer builds. It is a projection of contract records; it never invents a fact. Where a fact was
// not recorded the field is null and the screen prints the word unavailable, never a number.

import type {
  CakeView,
  Capability,
  ClockDomain,
  CommandType,
  Component,
  DataMode,
  NamedValue,
  PayloadPerceptionResult,
  PayloadRunFinished,
  PredicateEvidence,
  Provenance,
  RunFailure,
  RunPredicates,
  SnapshotActiveRun,
  SnapshotEvidence,
  SnapshotExecution,
  SnapshotLeaseStatus,
  SnapshotLink,
  SnapshotOwner,
  SnapshotRunEvidence,
  SnapshotTimings,
  SourceStamp,
} from '@kiwi/demo-contracts';
import type { UnresolvedCommand } from '../api/types';
import type { ContractEvent } from '../contracts/envelope';

/** How a shown number came to be. Every number on the pad scene carries one. */
export type Provenanced = 'measured' | 'inferred' | 'planned' | 'historical' | 'unavailable';

/** A break in the ordered stream. `declared` is true when the runtime announced it with a resync line. */
export interface StreamGap {
  from_seq: number;
  to_seq: number;
  declared: boolean;
}

export interface ObservationMeta {
  observation_id: string;
  source_role: 'wrist' | 'front' | 'base' | 'other';
  media: 'frame' | 'frame_pair' | 'scalar_set';
  width_px: number | null;
  height_px: number | null;
  content_digest: string | null;
  source_stamp: SourceStamp | null;
  retained: boolean;
  seq: number;
  t_wall_utc: string | null;
}

export interface ActionView {
  action_id: string | null;
  action_type: string;
  plan_only: boolean;
  parameters: NamedValue[];
  phase: 'planned' | 'started' | 'completed' | 'refused';
  result: 'ok' | 'stopped' | 'failed' | null;
  duration_ns: number | null;
  refusal: { reason_code: string | null; detail: string; before_motion: boolean } | null;
  seq: number;
}

/**
 * Which of the four states the controller put an evaluation in. Null means the RECORD DID NOT SAY, which is a fifth
 * fact and not one of the four: every predicate recorded before the field existed carries none, and for those the
 * older reading holds (a value is an evaluation, a null value was not evaluated).
 */
export type PredicateState = 'not_evaluated' | 'not_applicable' | 'unavailable' | 'evaluated' | null;

export interface PredicateView {
  predicate: string;
  value: boolean | null;
  state: PredicateState;
  /**
   * The payload's own reason, which the contract requires when the state is not_applicable or unavailable. It is NOT
   * the envelope's reason_code, which is what a console routes on, and the two are never merged.
   */
  reason_code: string | null;
  /** Which rule decided the verdict, and a digest of the thresholds it decided with. Null on a record that names none. */
  rule_ref: string | null;
  rule_digest: string | null;
  threshold: number | null;
  measured: number | null;
  unit: string | null;
  evidence: PredicateEvidence[];
  consumed_source_stamp: SourceStamp | null;
  /**
   * The values the controller decided this predicate with, beyond the one measured number. Empty for a record that
   * carried none, which is every record written before the field existed; an empty list is not a list of zeroes.
   */
  measurements: NamedValue[];
  seq: number;
  t_mono_ns: number;
  clock_domain: ClockDomain;
}

export interface PerceptionRecord {
  payload: PayloadPerceptionResult;
  provenance: Provenance | null;
  seq: number;
}

/** What the controller consumed to choose the action being shown. Pinned: see `reducer.ts`. */
export interface DecisionFrame {
  observation_ids: string[];
  primary_observation_id: string | null;
  consumed_by: { by: 'predicate' | 'action'; name: string; seq: number };
  perception: PerceptionRecord | null;
  capture: ObservationMeta | null;
  pinned_at_seq: number;
}

export interface MachineView {
  machine: string;
  state: string;
  from_state: string | null;
  since_seq: number;
  history: { state: string; seq: number }[];
}

export interface OutcomeView {
  /** Straight from the contract. The runtime decides it; the console never recomputes it. */
  reported: PayloadRunFinished['outcome'] | null;
  /**
   * `placed_new` ONLY when the reported outcome is placed_new AND all four end predicates measured true. Otherwise
   * null: an unconfirmed placement is never drawn as a confirmed one. This is a display rule, not a contradiction of
   * the contract, which keeps the physical outcome and the evidence separate on purpose.
   */
  confirmed: 'placed_new' | null;
  predicates: RunPredicates | null;
  exit_status: number | null;
  evidence_complete: boolean | null;
  evidence_gaps: number | null;
  /**
   * Null until a run has reported them. A zero on the screen before any run would be a count of something nobody
   * counted, and "no retries happened" is not a thing this console knows before a run finishes.
   */
  automatic_retries: number | null;
  task_control_interventions: number | null;
  run_id: string | null;
  finished_seq: number | null;
  /**
   * The machine-readable reason the run did not end well, derived by the controller where the decision was made. Null
   * for a run that ended as it should, and null for a record written before the field existed: "the record does not
   * say why" is a fact the screen states, and it is not the same as "it ended well".
   */
  reason_code: string | null;
  /** The state the machine was in when it stopped. */
  phase: string | null;
  /** The controller's own sentence, the values that decided it, and the observations in scope. */
  failure: RunFailure | null;
  /**
   * When the run finished, in the runtime's wall clock, as the runtime wrote it. Null for an outcome folded from a
   * `run.finished` record, which carries no wall time of its own, and null for a snapshot written before the field
   * existed. It exists so a PREVIOUS run's result can be shown as one, with the time it happened, instead of standing
   * in silently for the run that is on the screen now.
   */
  finished_wall_utc: string | null;
}

export interface StopView {
  /** Each record's own envelope wall time is kept as written (`t_wall_utc`), null when the record carried none. */
  requested: { origin: string; command_id: string | null; seq: number; t_mono_ns: number; t_wall_utc?: string | null } | null;
  acknowledged: { acknowledged_by: string; latency_ns: number | null; seq: number; t_wall_utc?: string | null } | null;
  outcome: { outcome: string; motion_ceased: boolean | null; evidence: string; seq: number; t_wall_utc?: string | null; reason_code?: string | null } | null;
}

/**
 * Every figure of ONE span in ONE clock domain.
 *
 * The domain is part of what identifies a row and not a label on it. A capture measured on the robot's computer and a
 * capture measured on this one are two different measurements of two different things, and adding them produces a
 * number nobody measured; the controller emits both under the name `capture`, so this is the live case and not a
 * hypothetical one. `count` is how many samples the figures rest on and the two sequence numbers bound the window
 * they were taken over, so no figure here can be read without knowing what it rests on. `max_ns` is the largest
 * sample seen inside that window: an observation, never a bound on the span.
 */
export interface TimingSpanView {
  span: string;
  count: number;
  total_ns: number;
  last_ns: number;
  max_ns: number;
  clock_domain: string;
  first_seq: number;
  last_seq: number;
}

export interface DiagnosticLine {
  seq: number;
  stream: string;
  level: string;
  text: string;
  redacted: boolean;
  reason_code: string | null;
}

/**
 * One producer session this fold contains, with the span of mission sequence numbers its records occupy.
 *
 * `first_seq` and `last_seq` are MISSION sequence numbers, from the runtime's one ordered log. The session's own
 * sequence space (a tap sample's `source_stamp.seq`, a preview's `seq`) counts something else entirely and is never
 * compared with these.
 */
export interface SourceSessionView {
  producer: string;
  session: string;
  first_seq: number;
  last_seq: number;
  records: number;
}

export interface EvidenceGapView {
  gap: string;
  detail: string;
  observation_ids: string[];
  seq: number;
}

/**
 * ONE POSE A CONTROLLER READ AND RECORDED. It is not telemetry and is never drawn as a feed.
 *
 * A controller writes one `measured.state` record when it reads the robot, so a non-motion Observe that moves nothing
 * records the whole pose as much as a cycle does. That is a different fact from the continuous telemetry tap, which on
 * this baseline is not configured at all, and printing the absent tap in place of this is how a measurement that HAD
 * been recorded came to be thrown away while the screen said the host was not reporting measurements.
 */
export interface MeasuredStateView {
  joints: Record<string, number | null>;
  base_velocity: { x_mps: number | null; y_mps: number | null; theta_dps: number | null };
  consumed_source_stamp: SourceStamp;
  seq: number;
  /**
   * When the RUNTIME WROTE THE RECORD, in its own wall clock, as it wrote it. It is the recording time and not the
   * instant the servo bus was sampled: relating those two needs a clock relation nothing on this path supplies. Null
   * for a record that carries none, which is not a time of zero.
   */
  t_wall_utc: string | null;
}

export interface RunConfigView {
  config_digest: string;
  summary: NamedValue[];
}

export interface ViewState {
  /** false once a `hello` reported an incompatible contract version: the tab is read-only from then on. */
  contract_compatible: boolean;
  /**
   * false when the server says this tab may not command, for a reason OTHER than the contract version: a replay-only
   * runtime, an unreachable runtime, a build without the hardware command path. The screen keeps following the record;
   * only the controls go away, with the server's reason.
   */
  commands_enabled: boolean;
  read_only_reason: string | null;
  /**
   * When the snapshot this state was last folded from reached THIS browser, on this computer's clock. Null until one
   * has arrived, which is not an age of zero and is never drawn as one.
   *
   * It is an ARRIVAL stamp and nothing else. Everything a snapshot carries was true at the runtime when the runtime
   * built it, and part of what it carries is itself an age measured over there: the reading the runtime takes about
   * the robot computer states its own age in the runtime's own words. The two are never added. What this field
   * answers is the question the screen could not otherwise answer at all, which is whether the capability list being
   * drawn arrived seconds ago or four minutes ago.
   */
  snapshot_at_ms: number | null;

  runtime_session: string | null;
  seq_cursor: number;
  retained_from_seq: number;
  /**
   * Where the data comes from. SEPARATE from `link`: a live view whose link dropped is still live, not replay. Null
   * until a snapshot has said it (spec 5.2 "Mode not recorded"): a runtime that has not answered is not a rehearsal.
   */
  data_mode: DataMode | null;
  link: SnapshotLink;
  owner: SnapshotOwner;
  /**
   * What the RUNTIME says about the host command lease, including why it does not hold one. `owner.lease` carries the
   * same three numbers but only while one is held; this is what says why one is not. Null when the runtime has
   * nothing to say about a lease, which is also what a runtime older than the field says by not sending it.
   */
  lease: SnapshotLeaseStatus | null;
  /**
   * Which path a command takes to the robot, and what actually owns the robot while one runs. It is the runtime's own
   * statement and the console never infers it: in particular, the absence of a lease is NOT read here as an absence of
   * ownership, because under the baseline backend no lease was ever asked for and the runtime's own single run slot is
   * the whole of the ownership. Null when the runtime said nothing about it, which is also what a replay says, since a
   * recording is folded from events and no event carries this.
   */
  execution: SnapshotExecution | null;
  active_run: SnapshotActiveRun | null;
  /**
   * The one command whose execution is NOT known, straight from the snapshot's additive `unresolved_command`. Null
   * means the runtime reported no block. It is snapshot-derived like `owner` and `link`, so it is never folded from an
   * event and never cleared by this console: the runtime is what decides that the block is gone.
   */
  unresolved: UnresolvedCommand | null;

  machine: MachineView | null;
  current_action: ActionView | null;
  action_history: ActionView[];
  decision_frame: DecisionFrame | null;
  predicates: PredicateView[];
  observations: Record<string, ObservationMeta>;
  /**
   * The newest retained frame the SNAPSHOT named, which is the only reference a tab that missed the capture has.
   *
   * Everything in `observations` is folded from `observation.captured` records off the event stream, so a tab that
   * connects or reloads after a capture has folded none of them and that map is empty. It was then the whole basis of
   * the camera card, which is how this screen came to say the controller had stored no frame while the frame was on
   * disk and the API was answering for it with a 200 and a jpeg. This field is the runtime's own answer to the same
   * question, and it is kept SEPARATE rather than merged into the map above: what was folded is fresher than a
   * snapshot built up to one poll ago, so the fold wins wherever it has anything and this is the fallback under it.
   * See `model/still.ts`, which is the one place the two are put in that order.
   */
  snapshot_observation: ObservationMeta | null;
  last_perception: PerceptionRecord | null;
  /** The newest perception result that named each observation id, so a pinned frame keeps the perception it consumed. */
  perception_by_observation: Record<string, PerceptionRecord>;
  measured_state: MeasuredStateView | null;
  /**
   * The newest `measured.state` record the SNAPSHOT named, which is the only reference a tab that folded no records
   * has. It is kept beside the folded one rather than merged into it, exactly as `snapshot_observation` is, and the
   * two are decided between BY SEQUENCE NUMBER and never by which arrived last: see `model/merge.ts` for why the
   * older rule of preferring the fold is wrong in both directions, and `model/measurement.ts` for the one place that
   * puts these two together.
   */
  snapshot_measured_state: MeasuredStateView | null;
  run_config: RunConfigView | null;
  /**
   * What the `run.started` record said, for the run this fold saw begin.
   *
   * `started_wall_utc` is the envelope's own wall time on that record, kept because it is the ONLY thing on this path
   * that can date the start of a run: `SnapshotActiveRun` carries a starting SEQUENCE NUMBER and no clock, and a
   * sequence number cannot be turned into a duration. It is null for a record written without one and, more often,
   * for a tab that joined after the run began and therefore folded no `run.started` at all. That case is a missing
   * start time and not an elapsed time of zero, and `model/mission.ts` is where the difference is said out loud.
   */
  run_started: {
    command_id: string | null;
    command_type: CommandType | null;
    plan_only: boolean;
    holding: boolean | null;
    started_wall_utc: string | null;
    /**
     * The run this record started and where it stands in the runtime's log. Optional: a record built by hand in a test
     * need not carry them, and absent reads as "not said", never as a run of its own. The fold always sets both.
     */
    run_id?: string | null;
    seq?: number | null;
  } | null;

  outcome: OutcomeView;
  /**
   * The snapshot's `last_outcome`, kept as its own fact and never merged into `outcome`.
   *
   * The runtime states plainly what this is: the most recent FINISHED run, which is the PREVIOUS run whenever a
   * different one is active. Folding it into `outcome` is how a screen ends up showing the last success as the result
   * of the run now in progress, and it is why the two are separate fields here. What the screen draws for the run it
   * is looking at is decided in `model/steps.ts`, by run id, and never by which of these two arrived last.
   */
  previous_outcome: OutcomeView | null;
  stop: StopView;

  evidence: SnapshotEvidence;
  evidence_gap_events: EvidenceGapView[];
  stream_gaps: StreamGap[];
  dropped_telemetry_events: { count: number; stage: string; first_seq: number | null; last_seq: number | null; seq: number }[];

  /** The spans THIS fold saw go past, keyed by span and clock domain. A replay has only these. */
  timings: TimingSpanView[];
  /**
   * The spans the RUNTIME aggregated for one run, which is a different and usually longer window than the one above:
   * a tab opened after a run began, or one that reconnected, saw only part of the stream, and the event ring the
   * runtime keeps is finite. Null from a runtime that aggregates none, which is not the same as a run with no spans.
   */
  runtime_timings: SnapshotTimings | null;
  /**
   * What the runtime recorded for the run in progress, kept apart from `evidence`, which counts the whole session.
   * Its `complete` is null until the run's own record states it: an absence of gaps is not a completed mission.
   */
  run_evidence: SnapshotRunEvidence | null;
  diagnostics: DiagnosticLine[];

  capabilities: Capability[];
  components: Component[];
  cake: CakeView;

  counters: {
    reconnects: number;
    runtime_restarts: number;
    malformed_lines: number;
    drop_notices: number;
    /** How often the runtime answered that this tab's cursor belongs to a log it is not serving any more. */
    cursor_replacements: number;
    /** Snapshots that arrived describing the runtime as it was BEFORE records already folded here, and were not applied. */
    stale_snapshots: number;
    /** Records the stream delivered twice, counted once and folded once. */
    duplicate_records: number;
    /** How often a producer already in this fold appeared with a different session. */
    source_session_changes: number;
  };

  last_event_seq: number | null;
  /** Which producer sessions this fold contains. See `noteSourceSession` in the reducer for why it is not a guard. */
  source_sessions: SourceSessionView[];
  /** A bounded tail of raw records, for the engineering view only. */
  recent_events: ContractEvent[];
}
