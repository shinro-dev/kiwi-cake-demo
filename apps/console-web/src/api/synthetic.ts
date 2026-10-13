// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The SYNTHETIC source: the whole console, demonstrable with no runtime, no robot and no network.
//
// It plays the scripted run of `syntheticScript.ts` at its own cadence, answers the same questions an HTTP runtime
// answers, and serves no frames: there is no image to serve, so the panels draw a marked synthetic scene instead of
// pretending a camera produced one. Everything it returns is marked `data_mode: "synthetic"`, and the badge and the
// watermark come from that.
//
// THE ONE-BLOCK SCENARIO (`?source=simulation&scenario=one-block`) is the one-block demo rehearsed inside the page
// (docs/autonomous-demo/one-block-demo-2026-09-26.md, section 6). It plays nothing on its own: Start begins a mission
// of one auto target, the measuring run ends before the pickup with mission_capability_required, the mission is held
// for the capability, the operator activates a prepared candidate (the abort-swap aborts and leaves the slot as it
// was; the swapped Plan is observed), the same target resumes, and the carrying run picks, pulses the base, places and
// returns. Every record is a rehearsal's and says so: the mission's data mode is synthetic and it performs no motion.
//
// THE SCENARIO CATALOGUE (`api/syntheticScenarios.ts`) generalizes that rehearsal: every scenario is a flow of scripted
// runs and what the mission does after each (complete, fail, hold for the capability, hold for the operator, go on),
// the candidates a held mission offers and what the simulated resident does with each, and optionally the robot, the
// wrist preview, Look, the scene scan and the capability slot check the runtime would report. The one-block rehearsal
// is the first entry of that catalogue and plays exactly as it did.

import type { Command, CommandResult, CommandType, Snapshot } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';
import type {
  ActivationOutcome,
  CakeStatusResult,
  CapabilityCheckOutcome,
  CapabilityCheckResult,
  SignatureCheckOutcome,
  SignatureCheckResult,
  ConsoleSource,
  EventsMessage,
  HelloResult,
  HistoryEntry,
  HistoryEventsResult,
  HistoryResult,
  MissionCandidatesResult,
  MissionResult,
  MissionStartOutcome,
  MissionStopOutcome,
  ObservationRead,
  RobotConfirmOutcome,
  RobotStatusResult,
  ResolutionOutcome,
  StopAck,
  StopMissionPart,
  SubmitOutcome,
  TelemetryMessage,
  Unsubscribe,
  RunOutputResult,
} from './types';
import type { HostInstance } from '../model/robotStatus';
import type { MissionActivationRecord, MissionRecord, MissionTargetRecord, SlotReading } from '../model/missionRecord';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import { parseSignatureCheckState } from '../model/signatureCheck';
import { parseRobotStatus } from '../model/robotStatus';
import type { SyntheticScenario } from '../panels/sourceChoice';
import { mintCommandId } from './commandId';
import { buildRun, buildStop, MISSION_RUNNING, SYNTHETIC_SESSION, SYNTHETIC_TAP_JOINTS, syntheticSnapshot } from './syntheticScript';
import type { ScriptStep, SyntheticRun } from './syntheticScript';
import {
  buildParkRun,
  HOLD_FOR_OPERATOR_STATE,
  INERT_CAPSULE,
  MOBILE_CAPSULE,
  OPERATOR_DECISION,
  PreviewScript,
  scenarioSpec,
  servedCandidates,
  syntheticRobotBody,
  syntheticSlot,
  TAMPERED_REFUSAL_CODE,
} from './syntheticScenarios';
import type { RunFactory, ScenarioAttemptRecord, ScenarioCandidate, ScenarioSpec } from './syntheticScenarios';
import { classifyOutcome, HOLDING_CLASSES, nextActions, RETRY_BUDGET_PER_TARGET, retryAllowed, stopStateFromEvents } from './syntheticRuntime';

const IDLE_BETWEEN_RUNS_MS = 5000;

/**
 * The capability slot as the rehearsal reads it: inert before the swap, the mobile capsule after, at `base` and at the
 * next generation. Every value invented.
 */
function slotReading(swapped: boolean, base = 3): SlotReading {
  const generation = swapped ? base + 1 : base;
  return {
    read: 'ok',
    slot_uuid: 'synthetic-capability-slot',
    name: 'mobile_approach',
    state: 'ACTIVE',
    capsule: swapped ? MOBILE_CAPSULE : INERT_CAPSULE,
    generation: String(generation),
    instance: String(generation),
    session_uuid: 'synthetic-resident-session',
    plan_digest: swapped ? `sha256:${'d4'.repeat(32)}` : `sha256:${'8b'.repeat(32)}`,
    epoch: String(generation + 1),
    capability_active: swapped,
  };
}

/** The runtime session the earlier-session signature check names: not this simulation's. Invented. */
const EARLIER_RUNTIME_SESSION = 'synthetic-runtime-session-earlier';

/**
 * Where a scenario's mission has got to. `running` is a run of the flow in progress, the two waits are the two holds,
 * `ended` covers complete, failed and stopped alike.
 */
type ScenarioPhase = 'running' | 'waiting_capability' | 'waiting_operator' | 'ended';

/** What the run in progress is for: a run of the mission's flow, the park before a retry, or a Look or scan alone. */
type RunRole = 'flow' | 'park' | 'standalone';

/** What one retry request came back as. The runtime's `POST /api/mission/retry` answers in the same three shapes. */
export type MissionRetryOutcome = { kind: 'started'; mission_id: string } | { kind: 'refused'; reason_code: string; detail: string | null };

/** The capability slot check of a scenario with no mission: the slot's generation, and every check recorded so far. */
interface CheckState {
  generation: number;
  active: boolean;
  checks: Record<string, unknown>[];
}

/**
 * The mission record as this source keeps it: the served record plus the attempts of the operator hold. `attempts` is
 * this source's own bookkeeping and never reaches the page: `servedMission` turns it into the runtime's shape (each
 * target's `attempts`, the `hold` while the mission waits for the operator, and the mission's `next_actions`).
 */
type ScenarioMissionRecord = MissionRecord & { attempts?: ScenarioAttemptRecord[]; retries?: Array<{ attempt: number; origin: string; t_wall_utc: string }>; parks?: string[] };

/** The command a run carries, as its own run.started names it; null when it names none. */
function commandTypeOf(events: readonly ContractEvent[]): CommandType | null {
  const started = events.find((event) => event.kind === 'run.started');
  return started?.kind === 'run.started' ? ((started.payload as { command_type?: CommandType | null }).command_type ?? null) : null;
}

/** mission.retry_asked_after: whether a Retry was asked for after `attempt`, that is for the hold it ended. */
function retryAskedAfter(record: ScenarioMissionRecord, attempt: ScenarioAttemptRecord | null): boolean {
  return attempt !== null && (record.retries ?? []).some((entry) => entry.attempt === attempt.attempt);
}

/** mission._mission_next_actions: what the operator can do now, for the whole mission. */
function missionNextActions(record: ScenarioMissionRecord, hold: MissionRecord['hold']): string[] {
  if (hold !== null && hold !== undefined) return [...hold.next_actions];
  if (record.state !== 'COMPLETE' && record.state !== 'FAILED' && record.state !== 'STOPPED') return ['stop'];
  if (record.state === 'COMPLETE') return ['start'];
  const attempts = record.attempts ?? [];
  const last = attempts[attempts.length - 1] ?? null;
  if (last === null) return nextActions('fatal', null, false);
  const stop = { ...last.stop_state };
  if (retryAskedAfter(record, last)) stop.moved = true;
  const ended = record.state === 'STOPPED' ? 'stopped' : HOLDING_CLASSES.has(last.outcome_class) ? 'fatal' : last.outcome_class;
  return nextActions(ended, stop, false);
}

/** The runtime's view of a scenario's mission (`demo_runtime/mission.py`, `mission_view` and `_hold_view`). */
function servedMission(record: ScenarioMissionRecord): MissionRecord {
  const { attempts: kept, retries: asked, parks, ...mission } = record;
  const attempts = kept ?? [];
  const last = attempts[attempts.length - 1] ?? null;
  const held = mission.state === HOLD_FOR_OPERATOR_STATE && mission.waiting_reason === OPERATOR_DECISION && last !== null;
  const hold = held && last !== null
    ? {
        target_id: mission.selected_target,
        attempt: last.attempt,
        run_id: last.run_id,
        park_run_id: last.park_run_id,
        outcome_class: last.outcome_class,
        reason_code: last.reason_code,
        phase: last.phase,
        measurements: last.measurements.map((item) => ({ ...item })),
        observation_ids: [...last.observation_ids],
        stop_state: { ...last.stop_state },
        next_actions: [...last.next_actions],
        budget_left: last.budget_left,
        t_wall_utc: last.t_wall_utc,
        retry_requested: retryAskedAfter(record, last),
      }
    : null;
  return {
    ...mission,
    targets: mission.targets.map((target, index) =>
      index === 0
        ? { ...target, attempts: attempts.map((item) => ({ ...item })), retries: (asked ?? []).map((item) => ({ ...item })), park_runs: [...(parks ?? [])] }
        : target,
    ),
    hold,
    next_actions: missionNextActions(record, hold),
  };
}

/** The resident session the capability check scenario reports. Invented. */
const CHECK_SESSION = 'synthetic-resident-session';

/** The placement mode the run's own TARGET_SELECTED line measured, or null when the run printed none. */
function measuredModeOf(events: readonly ContractEvent[]): string | null {
  const prefix = '[STATE] TARGET_SELECTED ';
  for (const event of [...events].reverse()) {
    if (event.kind !== 'diagnostic.text' || !event.payload.text.startsWith(prefix)) continue;
    try {
      const fields = JSON.parse(event.payload.text.slice(prefix.length)) as { mode?: unknown };
      return typeof fields.mode === 'string' ? fields.mode : null;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * The activation the simulated resident records for one candidate, by its scripted outcome. The abort-swap aborts
 * (the slot unchanged), the swapped Plan and the undo complete (the slot at the next generation, carrying the
 * capability or the inert capsule), and the tampered twin is refused before any transaction opens: no transaction,
 * no phase, the slot unchanged, and the refusal named for what it is, an integrity refusal.
 */
function activationRecord(candidate: ScenarioCandidate, index: number, targetId: string | null, before: SlotReading, after: SlotReading): MissionActivationRecord {
  const operation = `synthetic-operation-${String(index).padStart(4, '0')}`;
  const transaction = `synthetic-transaction-${String(index).padStart(4, '0')}`;
  const base = { operation_uuid: operation, target_id: targetId, candidate_key: candidate.key, payload_digest: candidate.payload_digest, t_wall_utc: new Date().toISOString(), slot_before: before, stopped_during_activation: false };
  if (candidate.outcome === 'refused_before_transaction') {
    return {
      ...base,
      activation_state: 'REFUSED_BEFORE_TRANSACTION',
      outcome: 'refused',
      transaction_phase: null,
      transaction_uuid: null,
      detail: `synthetic script: the resident refused this Plan before any transaction opened (${TAMPERED_REFUSAL_CODE}): its bytes do not match their digest, an integrity refusal; the slot, the session and the generation are unchanged`,
      slot_after: before,
      transaction: null,
    };
  }
  const completed = candidate.outcome !== 'rejected';
  const detail =
    candidate.outcome === 'observed'
      ? 'synthetic script: the slot read back carries the mobile capsule at the next generation; no resident was involved'
      : candidate.outcome === 'undo'
        ? 'synthetic script: the slot read back carries the inert capsule at the next generation, the capability inactive; no resident was involved'
        : 'synthetic script: the transaction aborted and the slot is unchanged; the answer says that it aborted and not why';
  return {
    ...base,
    activation_state: candidate.outcome === 'observed' ? 'OBSERVED' : candidate.outcome === 'undo' ? 'COMPLETED_NOT_OBSERVED' : 'REJECTED_BY_CAKE',
    outcome: candidate.outcome === 'observed' ? 'observed' : candidate.outcome === 'undo' ? 'completed' : 'rejected',
    transaction_phase: completed ? 'COMPLETED' : 'ABORTED',
    transaction_uuid: transaction,
    detail,
    slot_after: after,
    transaction: {
      operation_uuid: operation,
      transaction_uuid: transaction,
      transaction_handle: String(index),
      transaction_phase: completed ? 'COMPLETED' : 'ABORTED',
      status: 'ok',
      reason_code: null,
      answer_epoch: after.epoch,
    },
  };
}

export interface SyntheticOptions {
  /** The scenario to play, or null for the plain scripted run that plays on its own. */
  scenario?: SyntheticScenario | null;
}

export class SyntheticSource implements ConsoleSource {
  readonly kind = 'synthetic' as const;
  readonly label: string;

  /** The scenario this source plays, or null for the scripted run that plays on its own. */
  private readonly scenario: SyntheticScenario | null;
  private readonly spec: ScenarioSpec | null;
  private record: ScenarioMissionRecord | null = null;
  private phase: ScenarioPhase = 'ended';
  private missionIndex = 0;
  private activationIndex = 0;
  /** The step of the flow the mission is on: the run in progress, or the run a hold came after. */
  private flowIndex = 0;
  private runRole: RunRole = 'flow';
  private retriesUsed = 0;
  /** The park run a retry started, which the next attempt's record names. */
  private lastParkRunId: string | null = null;
  private robotConfirmed = false;
  private readonly preview: PreviewScript | null;
  /** Telemetry ticks left during which a decision capture is in flight; the preview stands aside for them. */
  private capturingTicks = 0;
  private check: CheckState = { generation: 1, active: false, checks: [] };
  /** Whether a swap was observed in this scenario's mission: the bench robot's capability slot follows it. */
  private swapped = false;
  /** When this-session's signature check ran, on this source's clock; set on the first read. */
  private signatureAt: string | null = null;

  constructor(options: SyntheticOptions = {}) {
    this.scenario = options.scenario ?? null;
    this.spec = scenarioSpec(this.scenario);
    this.preview = this.spec?.preview === undefined ? null : new PreviewScript(this.spec.preview);
    this.label = this.scenario === null ? 'built-in scripted run' : this.scenario === 'one-block' ? 'built-in one-block rehearsal' : `built-in scenario ${this.scenario}`;
  }

  private runIndex = 0;
  private seq = 0;
  private mono = 1_000_000_000;
  private run: SyntheticRun | null = null;
  private stepIndex = 0;
  private pending: ScriptStep[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private tapTimer: ReturnType<typeof setInterval> | null = null;
  private listeners = new Set<(message: EventsMessage) => void>();
  private telemetryListeners = new Set<(message: TelemetryMessage) => void>();
  private observations: Record<string, { record: ObservationRecordDto; perception: PerceptionDto }> = {};
  private state: string | null = null;
  private startedSeq = 0;
  private commandId: string | null = null;
  private lastOutcome: Snapshot['last_outcome'] = null;
  private finished: HistoryEntry[] = [];
  private finishedEvents: Record<string, ContractEvent[]> = {};
  private currentEvents: ContractEvent[] = [];
  private results: Record<string, CommandResult> = {};
  private tapSeq = 0;
  private stopping = false;

  get running(): boolean {
    return this.run !== null;
  }

  async hello(): Promise<HelloResult> {
    return { ok: true, contract_version: '1', runtime_session: SYNTHETIC_SESSION, reason_code: null, read_only: false, commands_enabled: true, credential_owner: null };
  }

  async snapshot(): Promise<Snapshot> {
    return syntheticSnapshot({
      seq_cursor: this.seq,
      retained_from_seq: 1,
      running: this.run !== null,
      run_id: this.run?.run_id ?? null,
      command_id: this.commandId,
      state: this.state,
      started_seq: this.startedSeq,
      last_outcome: this.lastOutcome,
      command_type: commandTypeOf(this.run?.steps.map((step) => step.event) ?? []),
      mission_carried: this.missionIsOpen(),
      motion_block: this.motionBlock(),
      refusals: this.standingRefusals(),
    });
  }

  /**
   * THE COMMANDS THIS SOURCE NEVER PLAYS, whatever the moment: Look where the scenario (or the plain script) has no Look,
   * Scan scene where it has no scan, and Start where the scenario begins with something else. The snapshot serves each
   * of them off with these words and `submit` refuses each with the same code, so no control is drawn on that the
   * rehearsal's runtime would refuse.
   */
  private standingRefusals(): Partial<Record<CommandType, { code: string; reason: string }>> {
    const refusals: Partial<Record<CommandType, { code: string; reason: string }>> = {};
    if (this.spec?.look === undefined) {
      refusals.observe = { code: 'synthetic_source', reason: 'this simulation plays no Look, so it refuses one' };
    }
    if (this.spec?.scan === undefined) {
      (refusals as Record<string, { code: string; reason: string }>).scan_scene = { code: 'synthetic_source', reason: 'this simulation plays no scene scan, so it refuses one' };
    }
    const start = this.spec?.start_refusal;
    if (start !== undefined) refusals.start_cycle = { code: start.reason_code, reason: start.detail };
    return refusals;
  }

  /** The robot's refusal of motion a bring-up scenario's runtime would apply to its capability list, or null. */
  private motionBlock(): { code: string; reason: string } | null {
    const robot = this.spec?.robot;
    if (robot === undefined || this.motionPermitted()) return null;
    const permission = (syntheticRobotBody(robot, this.robotConfirmed) as { motion_permission?: { reason_code?: unknown; detail?: unknown } }).motion_permission;
    const code = typeof permission?.reason_code === 'string' ? permission.reason_code : 'robot_session_not_confirmed';
    return { code, reason: typeof permission?.detail === 'string' ? permission.detail : 'robot motion is not permitted' };
  }

  subscribeEvents(_since: number, onMessage: (message: EventsMessage) => void): Unsubscribe {
    this.listeners.add(onMessage);
    // The plain scripted run plays on its own. A scenario plays nothing until Start is pressed.
    if (this.timer === null && this.run === null && this.scenario === null) this.schedule(IDLE_BETWEEN_RUNS_MS);
    return () => {
      this.listeners.delete(onMessage);
      if (this.listeners.size === 0) this.halt();
    };
  }

  subscribeTelemetry(onMessage: (message: TelemetryMessage) => void): Unsubscribe {
    this.telemetryListeners.add(onMessage);
    if (this.tapTimer === null) {
      this.tapTimer = setInterval(() => this.emitTelemetry(), 250);
    }
    return () => {
      this.telemetryListeners.delete(onMessage);
      if (this.telemetryListeners.size === 0 && this.tapTimer !== null) {
        clearInterval(this.tapTimer);
        this.tapTimer = null;
      }
    };
  }

  private emitTelemetry(): void {
    this.tapSeq += 1;
    const moving = this.run !== null;
    const message: TelemetryMessage = {
      tap: {
        // This tap is CONNECTED to a script inside this page and to nothing else, and its clock domains say so on both
        // sides: a synthetic sample never borrows the robot host's clock domain, because then a stamp from a script
        // would read on the screen exactly like a stamp from a robot.
        state: 'connected',
        available: true,
        reason: null,
        source_stamp: { session: 'synthetic-host-session', seq: this.tapSeq, sample_mono_ns: 2_000_000_000 + this.tapSeq * 25_000_000, clock_domain: 'laptop_monotonic' },
        clock_domains: { sample: 'laptop_monotonic', receive: 'laptop_monotonic' },
        joints: { ...SYNTHETIC_TAP_JOINTS },
        base_velocity: { x_mps: moving ? 0.118 : 0, y_mps: 0, theta_dps: 0 },
        receive_age_ms: 30 + (this.tapSeq % 7) * 3,
        measured_rate_hz: 4,
        subscriber_gap_count: 0,
        publisher_drop_count: 0,
        session_changes: 0,
        malformed_count: 0,
        transport_errors: 0,
      },
    };
    for (const listener of this.telemetryListeners) listener(message);
    if (this.preview !== null) {
      // A SCENARIO'S PREVIEW: the runtime's slot as the scenario scripts it. It still serves no picture.
      const sample = this.preview.next({ running: this.run !== null, capturing: this.capturingTicks > 0 });
      if (this.capturingTicks > 0) this.capturingTicks -= 1;
      for (const listener of this.telemetryListeners) listener({ preview: sample });
      return;
    }
    const preview: TelemetryMessage = {
      preview: {
        // NOT ATTACHED, and it says so with the runtime's own reason code: there is no camera anywhere in this path, so
        // this is not a camera that dropped out. Every measured field is null, because nothing measured one: a
        // synthetic fps over a stream that does not exist would be the one number on this screen nobody could check.
        state: 'disconnected',
        reason: 'preview_source_not_attached',
        role: 'wrist',
        seq: null,
        source_seq: null,
        capture_clock: null,
        capture_semantics: null,
        capture_age_ms: null,
        capture_age_reason: 'no_source_is_attached',
        service_latency_ms: null,
        receive_age_ms: null,
        measured_fps: null,
        frame: null,
        stream: null,
        gaps: null,
        session_changes: null,
        capture_time_stalls: null,
        malformed: null,
        available: false,
      },
    };
    for (const listener of this.telemetryListeners) listener(preview);
  }

  private publish(message: EventsMessage): void {
    for (const listener of this.listeners) listener(message);
  }

  private schedule(afterMs: number): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.tick(), afterMs);
  }

  private halt(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.tapTimer !== null) {
      clearInterval(this.tapTimer);
      this.tapTimer = null;
    }
  }

  private beginRun(commandId: string | null, build: RunFactory = buildRun): string {
    this.runIndex += 1;
    const run = build(this.runIndex, this.seq, this.mono);
    this.run = run;
    this.observations = { ...this.observations, ...run.observations };
    this.stepIndex = 0;
    this.pending = [];
    this.stopping = false;
    this.commandId = commandId;
    this.currentEvents = [];
    this.startedSeq = this.seq + 1;
    return run.run_id;
  }

  private finishRun(): void {
    const run = this.run;
    if (run !== null) {
      const last = this.currentEvents[this.currentEvents.length - 1];
      if (last !== undefined && last.kind === 'run.finished') {
        const outcome = last.payload;
        this.lastOutcome = { run_id: run.run_id, finished_seq: last.seq, result: outcome };
        this.finished = [
          {
            run_id: run.run_id,
            command_id: null,
            command_type: commandTypeOf(this.currentEvents),
            // The run's own records say when it started and finished; this source keeps no other clock for them.
            started_wall_utc: this.currentEvents.find((item) => item.kind === 'run.started')?.t_wall_utc ?? null,
            finished_wall_utc: last.t_wall_utc,
            outcome: outcome.outcome,
            exit_status: null,
            evidence_complete: outcome.evidence_complete,
            evidence_gaps: outcome.evidence_gaps,
            artifact_digest: `sha256:${'a1'.padStart(64, '0')}`,
            adopted: false,
            // This source drives a script, so every run it lists really is synthetic and says so. It is the one place
            // in this application that may fill the field in, because here it is not a guess about a recording.
            data_mode: 'synthetic' as const,
            bundle_digest: null,
          },
          ...this.finished,
        ].slice(0, 20);
      }
      this.finishedEvents[run.run_id] = this.currentEvents;
    }
    // Cleared BEFORE the scenario's next step, which may begin the next run of its flow at once.
    this.run = null;
    this.state = null;
    this.commandId = null;
    if (run !== null && this.spec !== null) {
      this.scenarioRunFinished(run.run_id, this.lastOutcome?.run_id === run.run_id ? this.lastOutcome.result.outcome : null, this.finishedEvents[run.run_id] ?? []);
    }
  }

  /* ---- a scenario's mission: the record the store reads back ---------------------------------------------------- */

  private target(): MissionTargetRecord | null {
    return this.record?.targets[0] ?? null;
  }

  /** One immutable step of the mission record. The store reads the record back; nothing here is mutated in place. */
  private setMission(patch: Partial<ScenarioMissionRecord>, targetPatch: Partial<MissionTargetRecord> = {}): void {
    const mission = this.record;
    if (mission === null) return;
    const targets = mission.targets.map((target, index) => (index === 0 ? { ...target, ...targetPatch } : target));
    this.record = { ...mission, ...patch, targets };
  }

  /**
   * WHAT THE RECORD SAYS ONCE A SCENARIO RUN ENDED, decided as the head's runtime decides it (mission_coordinator and the
   * robot-side execution at 05fcea0): a run that ended with mission_capability_required waits for the capability on this
   * target; a stopped run is an attempt of the STOPPED class; any other end is classified from the run's own record
   * (`syntheticRuntime.classifyOutcome`), written down as an attempt with what a press can do next, and the mission holds
   * for the operator on a holding class, completes on a placed run and fails on anything else. A park run is followed by
   * the flow step it parked for; a Look or a scan on its own changes no mission.
   */
  private scenarioRunFinished(runId: string, outcome: string | null, events: ContractEvent[]): void {
    const spec = this.spec;
    const role = this.runRole;
    this.runRole = 'flow';
    if (spec === null || this.record === null || role === 'standalone') return;
    const mode = measuredModeOf(events);
    if (mode !== null) this.setMission({}, { measured_mode: mode });
    const finished = [...events].reverse().find((event) => event.kind === 'run.finished');
    const payload = finished?.kind === 'run.finished' ? finished.payload : null;
    const stop = stopStateFromEvents(events);
    if (outcome === 'stopped' && this.record.stop_requested === true) {
      // A STOP THAT ENDED A RUN THE MISSION STARTED (RunStopped): an attempt of the STOPPED class with the run's stop state.
      // A stopped park writes nothing (the carrying thread returns on it).
      if (role === 'flow') this.recordAttempt(runId, 'stopped', null, null, [], [], stop, false);
      this.phase = 'ended';
      this.setMission({ state: 'STOPPED' }, { state: 'STOPPED' });
      return;
    }
    if (outcome === 'stopped' && role === 'flow') {
      // A STOP NOBODY ASKED FOR: the controller stopped the run itself (demo_runtime/mission_execution.py, lines 799-801).
      // The runtime writes an attempt of the stopped class with the run's reason code and phase, lists what the operator
      // does next from where the run left the robot, and fails the mission with the run's outcome in its detail.
      const reason = payload?.reason_code ?? null;
      const measurements = (payload?.failure?.measurements ?? []).map((item) => ({ name: item.name, value: item.value, unit: item.unit ?? null }));
      this.recordAttempt(runId, 'stopped', reason, payload?.phase ?? null, measurements, [...(payload?.failure?.observation_ids ?? [])], stop, false);
      this.phase = 'ended';
      this.setMission({ state: 'FAILED', waiting_reason: null, detail: `run_outcome:stopped:${reason ?? 'no_reason_recorded'}` }, { state: 'FAILED' });
      return;
    }
    if (role === 'park') {
      const step = spec.flow[this.flowIndex];
      if (step !== undefined) this.beginScenarioRun(step.build, 'flow');
      return;
    }
    if (payload?.reason_code === 'mission_capability_required') {
      // The block measured BASE_ADVANCE and the run carried no capability: the mission waits for it, on this target.
      this.phase = 'waiting_capability';
      this.setMission(
        {
          state: 'WAITING_FOR_CAPABILITY',
          waiting_reason: 'capability_unavailable',
          detail: `the block measured BASE_ADVANCE at SEARCH (run ${runId}); the mission waits for mobile_approach and comes back to this target`,
        },
        { state: 'WAITING_FOR_CAPABILITY', measured_mode: 'BASE_ADVANCE', reachable: false },
      );
      return;
    }
    const outcomeClass = classifyOutcome(
      { outcome: payload?.outcome ?? outcome, exit_status: payload?.exit_status ?? null, reason_code: payload?.reason_code ?? null, predicates: payload?.predicates ?? null },
      stop,
    );
    if (outcomeClass === 'placed') {
      this.phase = 'ended';
      this.setMission({ state: 'COMPLETE', waiting_reason: null, detail: null }, { state: 'PLACED' });
      return;
    }
    // WRITTEN DOWN WHATEVER FOLLOWS: what this attempt came to, where it left the robot, and what a press can do.
    const measurements = (payload?.failure?.measurements ?? []).map((item) => ({ name: item.name, value: item.value, unit: item.unit ?? null }));
    // The runtime at the head can park (Supervisor.start_mission_park), so a retry is offered while the budget lasts and
    // the ending is one a Retry may follow (mission_execution.retry_allowed).
    // A runtime that admits no Retry at all (mission_retry_refusal, the LIVE default) offers none either.
    const retryLeft = this.retriesUsed < RETRY_BUDGET_PER_TARGET && retryAllowed(payload?.reason_code ?? null) && this.spec?.retry_refusal === undefined;
    this.recordAttempt(runId, outcomeClass, payload?.reason_code ?? null, payload?.phase ?? null, measurements, [...(payload?.failure?.observation_ids ?? [])], stop, retryLeft);
    if (HOLDING_CLASSES.has(outcomeClass)) {
      // THE HOLD: the mission waits for the operator, and nothing moves until a press.
      this.phase = 'waiting_operator';
      this.setMission(
        // mission.target_waiting carries the reason and no detail: what the attempt came to is in the hold.
        { state: HOLD_FOR_OPERATOR_STATE, waiting_reason: OPERATOR_DECISION },
        { state: HOLD_FOR_OPERATOR_STATE },
      );
      return;
    }
    // Any other end: the mission failed on this target, and the record says which run.
    this.phase = 'ended';
    this.setMission({ state: 'FAILED', waiting_reason: null, detail: `run ${runId} ended ${outcome ?? 'without an outcome'}` }, { state: 'FAILED' });
  }

  /** mission.target_attempt: one attempt of the mission's target, with the next actions and the budget the runtime writes. */
  private recordAttempt(
    runId: string,
    outcomeClass: string,
    reasonCode: string | null,
    phase: string | null,
    measurements: ScenarioAttemptRecord['measurements'],
    observationIds: string[],
    stop: ScenarioAttemptRecord['stop_state'],
    retryLeft: boolean,
  ): void {
    const attempts = this.record?.attempts ?? [];
    const previous = attempts[attempts.length - 1];
    // The park that prepared this attempt: the one started after the previous attempt's retry, when there was one.
    const park = previous !== undefined && (this.record?.retries ?? []).some((entry) => entry.attempt === previous.attempt) ? this.lastParkRunId : null;
    const attempt: ScenarioAttemptRecord = {
      attempt: attempts.length + 1,
      run_id: runId,
      park_run_id: park,
      outcome_class: outcomeClass,
      reason_code: reasonCode,
      phase,
      measurements: measurements.slice(0, 32),
      observation_ids: observationIds.slice(0, 32),
      stop_state: stop,
      next_actions: nextActions(outcomeClass, stop, retryLeft),
      budget_left: RETRY_BUDGET_PER_TARGET - this.retriesUsed,
      t_wall_utc: new Date().toISOString(),
    };
    this.setMission({ attempts: [...attempts, attempt] });
  }

  /** Begin one scenario run against the mission's target, and list it under that target. */
  private beginScenarioRun(build: RunFactory, role: RunRole): string {
    const runId = this.beginRun(null, build);
    this.runRole = role;
    const target = this.target();
    this.setMission({}, { state: 'ACTIVE', runs: [...(target?.runs ?? []), runId] });
    this.schedule(200);
    return runId;
  }

  private missionIsOpen(): boolean {
    const state = this.record?.state;
    return state === 'RUNNING' || state === 'WAITING_FOR_CAPABILITY' || state === HOLD_FOR_OPERATOR_STATE;
  }

  /** The mission's part of a stop: asked for, and what the record then says. */
  private stopMission(): StopMissionPart | null {
    const mission = this.record;
    if (mission === null || !this.missionIsOpen()) return null;
    const carried = this.run !== null;
    this.phase = 'ended';
    // mission.mission_ended writes the state only: the waiting reason and the selected target stay on the record.
    this.setMission({ state: 'STOPPED', stop_requested: true, stop_requested_wall_utc: new Date().toISOString() }, { state: 'STOPPED' });
    return { mission_id: mission.mission_id, stopping: true, reason_code: null, state: 'STOPPED', carried };
  }

  /** Whether the scenario's robot would let a motion command through now. True for a scenario with no robot. */
  private motionPermitted(): boolean {
    const robot = this.spec?.robot;
    if (robot === undefined || robot === 'bench') return true;
    return robot === 'not_confirmed' && this.robotConfirmed;
  }

  private tick(): void {
    this.timer = null;
    if (this.run === null) {
      // A scenario never starts a run on its own; the plain scripted run does.
      if (this.scenario !== null) return;
      this.beginRun(null);
    }
    const run = this.run;
    if (run === null) return;

    const queue = this.pending.length > 0 ? this.pending : run.steps;
    const usingPending = this.pending.length > 0;
    const index = usingPending ? 0 : this.stepIndex;
    const step = queue[index];
    if (step === undefined) {
      this.finishRun();
      if (this.scenario === null) this.schedule(IDLE_BETWEEN_RUNS_MS);
      return;
    }

    if (usingPending) this.pending = this.pending.slice(1);
    else this.stepIndex += 1;

    // RECORDED WHEN IT IS RECORDED: the runtime stamps each record's wall time as it writes it, and this source does the
    // same on its own clock as it plays the step. A scripted step carries no time of its own.
    const event: ContractEvent = step.event.t_wall_utc === null ? ({ ...step.event, t_wall_utc: new Date().toISOString() } as ContractEvent) : step.event;
    this.seq = Math.max(this.seq, event.seq);
    this.mono = Math.max(this.mono, event.t_mono_ns);
    if (event.kind === 'state.transition') this.state = event.payload.to_state;
    // A decision capture is in flight: a scenario's preview stands aside for about three ticks, as the camera service does.
    if (event.kind === 'observation.captured') this.capturingTicks = 3;
    this.currentEvents.push(event);
    this.publish({ event });

    // The script skipped this range of seq on purpose and never sent anything for it. The runtime announces it here,
    // so the gap on the screen is a gap that really exists.
    if (!usingPending && run.resync !== null && this.stepIndex === run.resync.after_step) {
      this.publish({ resync: { retained_from_seq: run.resync.retained_from_seq, missed: run.resync.missed, reason: null } });
    }

    if (event.kind === 'run.finished') {
      this.finishRun();
      if (this.scenario === null) this.schedule(IDLE_BETWEEN_RUNS_MS);
      return;
    }

    const next = usingPending ? this.pending[0] : run.steps[this.stepIndex];
    this.schedule(next?.afterMs ?? 400);
  }

  async submit(commandId: string, command: Command): Promise<SubmitOutcome> {
    // WHILE A MISSION IS CARRIED, HELD INCLUDED, THE RUNTIME TAKES ONLY STOP: any command is refused with the run that
    // is active, else with the mission's reason (supervisor.block, run_active before mission_running).
    if (this.run === null && this.missionIsOpen()) {
      const busy: CommandResult = { command_id: commandId, status: 'rejected', reason_code: MISSION_RUNNING, run_id: null };
      this.results[commandId] = busy;
      return { kind: 'result', result: busy };
    }
    // THE FOUR COMMANDS THE SNAPSHOT SERVES, refused in the order it serves them: the run in progress, what this script
    // never plays, the robot's motion refusal (never for Look). A command served on is admitted below.
    const type = command.type as string;
    if (type === 'start_cycle' || type === 'observe' || type === 'return_arm' || type === 'scan_scene') {
      const standing = (this.standingRefusals() as Record<string, { code: string; reason: string }>)[type];
      const motion = type === 'observe' ? null : this.motionBlock();
      const code = this.run !== null ? 'run_active' : standing?.code ?? motion?.code ?? null;
      if (code !== null) {
        const refused: CommandResult = { command_id: commandId, status: 'rejected', reason_code: code, run_id: this.run?.run_id ?? null };
        this.results[commandId] = refused;
        return { kind: 'result', result: refused };
      }
    }
    if (command.type === 'start_cycle') {
      const runId = this.beginRun(commandId);
      this.runRole = 'standalone';
      const accepted: CommandResult = { command_id: commandId, status: 'running', reason_code: null, run_id: runId };
      this.results[commandId] = accepted;
      this.schedule(200);
      return { kind: 'result', result: accepted };
    }
    // A SCENARIO'S LOOK AND ITS SCENE SCAN, each a run of its own that changes no mission. The scan's command type is
    // compared as text: it joins the contract's command union with track P0b, and the simulation answers it already.
    // RETURN ARM in a scenario is the controller's `--do exit`, the same run the runtime starts for it and for a park.
    const standalone =
      command.type === 'observe'
        ? this.spec?.look
        : (command.type as string) === 'scan_scene'
          ? this.spec?.scan
          : command.type === 'return_arm' && this.spec !== undefined
            ? buildParkRun
            : undefined;
    if (standalone !== undefined) {
      if (this.run !== null || this.missionIsOpen()) {
        const busy: CommandResult = { command_id: commandId, status: 'rejected', reason_code: this.run !== null ? 'run_active' : MISSION_RUNNING, run_id: this.run?.run_id ?? null };
        this.results[commandId] = busy;
        return { kind: 'result', result: busy };
      }
      const runId = this.beginRun(commandId, standalone);
      this.runRole = 'standalone';
      const accepted: CommandResult = { command_id: commandId, status: 'running', reason_code: null, run_id: runId };
      this.results[commandId] = accepted;
      this.schedule(200);
      return { kind: 'result', result: accepted };
    }
    const rejected: CommandResult = {
      command_id: commandId,
      status: 'rejected',
      reason_code: 'synthetic_source',
      run_id: null,
    };
    this.results[commandId] = rejected;
    return { kind: 'result', result: rejected };
  }

  async resolve(commandId: string): Promise<CommandResult | null> {
    return this.results[commandId] ?? null;
  }

  /**
   * There is no runtime here, so there is no process table, no run directory and no observation store to read. This
   * source therefore REFUSES to reconcile rather than answering that it found nothing: "no machine evidence exists"
   * and "the machine evidence says nothing ran" are different facts, and a script inside a browser is not entitled to
   * the second one.
   */
  async resolveUncertain(): Promise<ResolutionOutcome> {
    return {
      kind: 'answered',
      cleared: false,
      cleared_by: null,
      classification: 'still_unknown',
      reason_code: 'synthetic_source',
      evidence: null,
      attestation: null,
      contradiction: null,
    };
  }

  async stop(): Promise<StopAck> {
    // The id is minted here as the live source mints one, so a caller sees the same shape from both: a stop is a
    // command with an identity, and this source carries no journal to look it up in afterwards.
    const commandId = mintCommandId();
    const run = this.run;
    if (run === null) {
      // A HELD MISSION IS STOPPED WITH NO RUN TO STOP, exactly as the runtime's unified stop does it: the mission part
      // carries the stop, and the stop is taken on the strength of it.
      const mission = this.stopMission();
      if (mission !== null) {
        return { ok: true, command_id: commandId, result: null, requested_mono_ns: this.mono, run_id: null, reason: null, outcome: 'taken', mission };
      }
      // There is nothing to stop. That is a refusal with a reason, not a stop that was taken.
      return { ok: false, command_id: commandId, result: null, requested_mono_ns: null, run_id: null, reason: 'no run is in progress', outcome: 'refused', mission: null };
    }
    // The mission part is null for the plain scripted run, which carries no mission; the scenario's mission says STOPPED.
    const taken: StopAck = { ok: true, command_id: commandId, result: null, requested_mono_ns: this.mono, run_id: run.run_id, reason: null, outcome: 'taken', mission: this.stopMission() };
    if (this.stopping) return taken;
    this.stopping = true;
    this.pending = buildStop(run.run_id, this.seq, this.mono, this.commandId);
    this.schedule(10);
    return taken;
  }

  async observationRecord(observationId: string): Promise<ObservationRead<ObservationRecordDto>> {
    const record = this.observations[observationId]?.record ?? null;
    return record === null ? { data: null, reason: 'the synthetic source holds no record for it' } : { data: record, reason: null };
  }

  async observationPerception(observationId: string): Promise<ObservationRead<PerceptionDto>> {
    const perception = this.observations[observationId]?.perception ?? null;
    return perception === null ? { data: null, reason: 'the synthetic source holds no perception for it' } : { data: perception, reason: null };
  }

  frameUrl(): string | null {
    // There is no stored image. A synthetic source that served a picture would be inviting the screen to be read as a
    // photograph of something that happened.
    return null;
  }

  previewUrl(): string | null {
    return null;
  }

  async history(): Promise<HistoryResult> {
    return { ok: true, entries: this.finished };
  }

  async runOutput(runId: string, stream: 'stdout' | 'stderr'): Promise<RunOutputResult> {
    // The scripted run inside this page has no controller and therefore no controller output. It says so, rather
    // than returning an empty log that would read as a run that printed nothing.
    return { ok: false, reason_code: `this is the simulation inside the page: it runs no controller, so there is no ${stream} for ${runId}` };
  }

  async cakeStatus(): Promise<CakeStatusResult> {
    // This source speaks to no resident and reads no admin socket. It says so, rather than answering with an invented
    // one: a scripted resident identity here would be exactly the placeholder inventory that surface exists to keep
    // off the screen.
    return { ok: false, reason_code: 'this is the simulation inside the page: it speaks to no Cake resident' };
  }

  /**
   * THE PLAIN SCRIPTED RUN IS ONE CUBE AND CARRIES NO MISSION. It says so, rather than answering with a scripted
   * mission: two invented targets and an invented activation history here would be the placeholder inventory that the
   * whole Cake surface exists to keep off the screen, told one layer up.
   *
   * THE ONE-BLOCK SCENARIO CARRIES ONE, and it is a rehearsal's record: data mode synthetic, performs no motion, one auto
   * target. Before Start it answers `no_mission_recorded`, which is the one state a first mission may be started in.
   */
  async mission(): Promise<MissionResult> {
    if (this.scenario === null) return { ok: false, reason_code: 'this is the simulation inside the page: it carries no mission' };
    if (this.record === null) return { ok: false, reason_code: 'no_mission_recorded' };
    return { ok: true, mission: servedMission(this.record) };
  }

  async missionCandidates(): Promise<MissionCandidatesResult> {
    if (this.scenario === null) return { ok: false, reason_code: 'this is the simulation inside the page: it prepares no candidates' };
    return { ok: true, candidates: servedCandidates(this.spec?.candidates ?? []) };
  }

  /**
   * ONE PREPARED CANDIDATE, BY KEY, for a held mission of a scenario. What the simulated resident does with it is the
   * candidate's scripted outcome: the abort-swap aborts the transaction and leaves the slot as it was
   * (REJECTED_BY_CAKE, the mission still held); the tampered twin is refused before any transaction opens
   * (REFUSED_BEFORE_TRANSACTION, an integrity refusal, the mission still held); the swapped Plan is read back in the
   * slot (OBSERVED) and the same target resumes with the next run of the flow. The answer is a receipt; the verdict is
   * the record.
   */
  async activateCandidate(candidateKey: string): Promise<ActivationOutcome> {
    if (this.scenario === null) {
      // Refused, not unknown: this source is in the page and certainly reached no resident.
      return { kind: 'refused', reason_code: 'this is the simulation inside the page: it speaks to no Cake resident' };
    }
    const mission = this.record;
    if (mission === null || mission.state !== 'WAITING_FOR_CAPABILITY' || this.phase !== 'waiting_capability') {
      return { kind: 'refused', reason_code: 'mission_not_waiting' };
    }
    if (mission.stop_requested === true) return { kind: 'refused', reason_code: 'mission_stopping' };
    const candidate = (this.spec?.candidates ?? []).find((item) => item.key === candidateKey);
    if (candidate === undefined) return { kind: 'refused', reason_code: 'unknown_candidate' };
    if (candidate.outcome === 'undo') return { kind: 'refused', reason_code: 'candidate_not_for_a_held_mission' };
    this.activationIndex += 1;
    const base = this.spec?.slot_generation ?? 3;
    const record = activationRecord(candidate, this.activationIndex, this.target()?.target_id ?? null, slotReading(this.swapped, base), candidate.outcome === 'observed' ? slotReading(true, base) : slotReading(this.swapped, base));
    this.setMission({ activations: [...mission.activations, record] });
    if (candidate.outcome === 'observed') {
      this.swapped = true;
      // THE SAME TARGET RESUMES: ACTIVE again, the mission RUNNING, the waiting reason cleared, and the next run begins.
      const next = this.spec?.flow[this.flowIndex + 1];
      this.phase = 'running';
      this.setMission({ state: 'RUNNING', waiting_reason: null, detail: null }, { state: 'ACTIVE' });
      if (next !== undefined) {
        this.flowIndex += 1;
        this.beginScenarioRun(next.build, 'flow');
      }
    }
    return { kind: 'answered', operation_uuid: record.operation_uuid, activation_state: record.activation_state, outcome: record.outcome, transaction_phase: record.transaction_phase, detail: record.detail };
  }

  /**
   * THE PLAIN SCRIPT CARRIES NO MISSION, SO IT STARTS NONE. Refused with the same sentence the mission read gives, so
   * the Start control is off there for the reason the screen already states.
   *
   * A SCENARIO STARTS ITS MISSION HERE, and nowhere else: one auto target, the first run of its flow. A second Start
   * while one is open is refused the way the runtime refuses it (mission_already_running); a scenario whose robot is
   * not confirmed is refused the way the runtime refuses motion (robot_session_not_confirmed); a scenario that begins
   * with Look or with the capability check refuses Start with its own reason.
   */
  async missionStart(): Promise<MissionStartOutcome> {
    const spec = this.spec;
    if (this.scenario === null || spec === null) return { kind: 'refused', reason_code: 'this is the simulation inside the page: it carries no mission', detail: null };
    if (spec.start_refusal !== undefined) return { kind: 'refused', reason_code: spec.start_refusal.reason_code, detail: spec.start_refusal.detail };
    if (!this.motionPermitted()) {
      const robot = await this.robotStatus();
      return { kind: 'refused', reason_code: 'robot_session_not_confirmed', detail: robot.ok ? robot.status.motion_permission.detail : null };
    }
    if (this.missionIsOpen()) return { kind: 'refused', reason_code: 'mission_already_running', detail: null };
    if (this.run !== null) return { kind: 'refused', reason_code: 'run_active', detail: null };
    const first = spec.flow[0];
    if (first === undefined) return { kind: 'refused', reason_code: 'scenario_has_no_mission', detail: null };
    this.missionIndex += 1;
    const missionId = `synthetic-mission-${String(this.missionIndex).padStart(4, '0')}`;
    this.record = {
      mission_record_version: '1',
      mission_id: missionId,
      state: 'RUNNING',
      data_mode: 'synthetic',
      performs_motion: false,
      execution_label: 'synthetic-script',
      started_wall_utc: new Date().toISOString(),
      selected_target: 'cube',
      waiting_reason: null,
      detail: null,
      stop_requested: false,
      stop_requested_wall_utc: null,
      records_after_end: 0,
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', measured_mode: null, state: 'ACTIVE', runs: [] }],
      activations: [],
      attempts: [],
      retries: [],
      parks: [],
    };
    this.phase = 'running';
    this.flowIndex = 0;
    this.retriesUsed = 0;
    this.lastParkRunId = null;
    this.beginScenarioRun(first.build, 'flow');
    return { kind: 'started', mission_id: missionId };
  }

  /**
   * RETRY A MISSION HELD FOR THE OPERATOR (track R: `POST /api/mission/retry`). The arm is parked first in a run of its
   * own when the flow says so, then a fresh attempt runs with its own record. It is not on `ConsoleSource` yet: the
   * console's retry button is wired when that interface carries it, and until then this is how the scenario is driven.
   */
  async missionRetry(): Promise<MissionRetryOutcome> {
    const spec = this.spec;
    const mission = this.record;
    if (spec === null || mission === null) return { kind: 'refused', reason_code: 'mission_not_held', detail: null };
    // mission_coordinator.request_retry, in its order: a stop asked for, a mission not held, the runtime's own refusal, a
    // held attempt that offers no retry, a retry already asked for this hold.
    if (mission.stop_requested === true) return { kind: 'refused', reason_code: 'mission_stopping', detail: null };
    if (this.phase !== 'waiting_operator' || mission.state !== HOLD_FOR_OPERATOR_STATE || mission.waiting_reason !== OPERATOR_DECISION) {
      return { kind: 'refused', reason_code: 'mission_not_held', detail: null };
    }
    // mission_coordinator.request_retry asks the runtime's own refusal before the held attempt's list.
    if (spec.retry_refusal !== undefined) return { kind: 'refused', reason_code: spec.retry_refusal, detail: null };
    const held = mission.attempts?.[mission.attempts.length - 1];
    if (held === undefined || !held.next_actions.some((action) => action === 'retry' || action === 'move_block_then_retry')) {
      return { kind: 'refused', reason_code: 'mission_retry_not_offered', detail: null };
    }
    if (retryAskedAfter(mission, held)) return { kind: 'refused', reason_code: 'mission_retry_already_requested', detail: null };
    // Written down first (mission.retry_requested), then the target resumes, the arm is parked and a fresh attempt runs.
    this.setMission({ retries: [...(mission.retries ?? []), { attempt: held.attempt, origin: 'operator_console', t_wall_utc: new Date().toISOString() }] });
    this.retriesUsed += 1;
    // The fresh attempt is the next step of the flow; a flow with no further step runs its last step again.
    if (spec.flow[this.flowIndex + 1] !== undefined) this.flowIndex += 1;
    this.phase = 'running';
    this.setMission({ state: 'RUNNING', waiting_reason: null }, { state: 'ACTIVE' });
    // mission.target_run_started with run_kind park: the park is listed among the target's runs and its park runs.
    this.lastParkRunId = this.beginScenarioRun(buildParkRun, 'park');
    this.setMission({ parks: [...(this.record?.parks ?? []), this.lastParkRunId] });
    return { kind: 'started', mission_id: mission.mission_id };
  }

  async missionStop(): Promise<MissionStopOutcome> {
    if (this.scenario === null) return { kind: 'refused', reason_code: 'this is the simulation inside the page: it carries no mission', detail: null };
    if (!this.missionIsOpen()) return { kind: 'refused', reason_code: 'no_mission_running', detail: null };
    const runStopped = this.run !== null;
    const ack = await this.stop();
    if (ack.mission === null) return { kind: 'refused', reason_code: ack.reason ?? 'no_mission_running', detail: null };
    return { kind: 'answered', mission: ack.mission, run_stopped: runStopped };
  }

  /**
   * The script inside this page has no robot, and says so. A BRING-UP SCENARIO reports the robot its day had, in the
   * runtime's own shape and marked as the simulation's in its display label: a robot not yet confirmed, or a host
   * running outside Cake.
   */
  async robotStatus(): Promise<RobotStatusResult> {
    const robot = this.spec?.robot;
    if (robot === undefined) return { ok: false, reason_code: 'this is the simulation inside the page: it is connected to no robot' };
    // THE BENCH ROBOT'S CAPABILITY SLOT IS THE SCENARIO'S OWN: the capability check's in a check scenario, else the
    // slot the mission's activations moved.
    const checkScenario = this.spec?.check_candidates !== undefined;
    const base = this.spec?.slot_generation ?? 3;
    const bench =
      robot !== 'bench'
        ? null
        : checkScenario
          ? { generation: this.check.generation, active: this.check.active, capsule: this.check.active ? MOBILE_CAPSULE : INERT_CAPSULE, hostHeld: true }
          : { generation: this.swapped ? base + 1 : base, active: this.swapped, capsule: this.swapped ? MOBILE_CAPSULE : INERT_CAPSULE, hostHeld: false };
    const status = parseRobotStatus(syntheticRobotBody(robot, this.robotConfirmed, bench));
    if (status === null) return { ok: false, reason_code: 'robot_status_unreadable' };
    return { ok: true, status };
  }

  async confirmRobotSession(sessionUuid?: string, hostInstance?: HostInstance): Promise<RobotConfirmOutcome> {
    const robot = this.spec?.robot;
    if (robot === undefined) return { kind: 'refused', reason_code: 'this is the simulation inside the page: there is no robot session to confirm' };
    const current = await this.robotStatus();
    const answer = (ok: boolean, reason: string | null): RobotConfirmOutcome => ({
      kind: 'answered',
      ok,
      reason_code: reason,
      confirmed_session_uuid: ok ? (sessionUuid ?? null) : null,
      confirmed_host_instance: ok && hostInstance !== undefined ? { ...hostInstance } : null,
      confirmed_wall_utc: ok ? new Date().toISOString() : null,
      motion_permitted: ok,
    });
    if (robot === 'host_outside_cake' || !current.ok) return answer(false, 'robot_host_not_running');
    const permission = current.status.motion_permission;
    const instance = permission.current_host_instance;
    const matches =
      sessionUuid === permission.current_session_uuid &&
      instance !== null &&
      hostInstance !== undefined &&
      hostInstance.slot_instance === instance.slot_instance &&
      hostInstance.spawn_sequence === instance.spawn_sequence &&
      hostInstance.pid === instance.pid;
    if (!matches) return answer(false, 'robot_session_changed');
    this.robotConfirmed = true;
    return answer(true, null);
  }

  /**
   * No robot, so no capability slot to check: said, never drawn as a check with nothing in it. A CHECK SCENARIO
   * reports the simulated resident's slot with the host held and no mission, and every check applied so far.
   */
  async capabilityCheck(): Promise<CapabilityCheckResult> {
    const candidates = this.spec?.check_candidates;
    if (candidates === undefined) return { ok: false, reason_code: 'this is the simulation inside the page: it reaches no robot resident', detail: null };
    const state = parseCapabilityCheckState({
      ok: true,
      capability_check_version: '1',
      in_flight: false,
      pending_operation_uuid: null,
      off_reason: this.missionIsOpen() ? 'mission_running' : this.run !== null ? 'run_active' : null,
      candidates: servedCandidates(candidates),
      current: {
        session_uuid: CHECK_SESSION,
        build_identity: '5e5e5e5e5e5e5e5e',
        host_running: false,
        host_held: true,
        host_reason_code: null,
        slot: syntheticSlot(this.check.generation, this.check.active),
        observed_wall_utc: new Date().toISOString(),
        age_ms: 300,
      },
      checks: this.check.checks,
      checks_dropped: 0,
    });
    if (state === null) return { ok: false, reason_code: 'capability_check_unreadable', detail: null };
    return { ok: true, state };
  }

  /** Apply one prepared Plan of a check scenario, by key, with the digest and session shown. The receipt; the record is read back. */
  async applyCapabilityCheck(candidateKey?: string, payloadDigest?: string, sessionUuid?: string): Promise<CapabilityCheckOutcome> {
    const candidates = this.spec?.check_candidates;
    // Refused, not unknown: this source is in the page and certainly reached no resident.
    if (candidates === undefined) return { kind: 'refused', reason_code: 'this is the simulation inside the page: it speaks to no Cake resident', detail: null };
    const candidate = candidates.find((item) => item.key === candidateKey);
    if (candidate === undefined) return { kind: 'refused', reason_code: 'unknown_candidate', detail: null };
    if (payloadDigest !== candidate.payload_digest) return { kind: 'refused', reason_code: 'payload_digest_mismatch', detail: null };
    if (sessionUuid !== CHECK_SESSION) return { kind: 'refused', reason_code: 'robot_session_mismatch', detail: null };
    if (this.missionIsOpen() || this.run !== null) return { kind: 'refused', reason_code: 'mission_running', detail: null };
    this.activationIndex += 1;
    const before = { generation: this.check.generation, active: this.check.active };
    const moves = candidate.outcome === 'observed' || candidate.outcome === 'undo';
    if (moves) this.check = { ...this.check, generation: this.check.generation + 1, active: candidate.outcome === 'observed' };
    const record = activationRecord(
      candidate,
      this.activationIndex,
      null,
      syntheticSlot(before.generation, before.active) as unknown as SlotReading,
      syntheticSlot(this.check.generation, this.check.active) as unknown as SlotReading,
    );
    const host = { session_uuid: CHECK_SESSION, build_identity: '5e5e5e5e5e5e5e5e', host_running: false, held: true, reason_code: null, observed_wall_utc: new Date().toISOString() };
    this.check = {
      ...this.check,
      checks: [
        ...this.check.checks,
        {
          ...record,
          requested_session_uuid: sessionUuid,
          host_before: host,
          host_after: host,
          host_held_throughout: true,
          host_throughout_detail: null,
          unresolved: false,
          resident_session_changed: false,
          slot_changed: moves,
          answered: true,
          orphaned: false,
          duration_ms: 180,
          answer_wall_utc: new Date().toISOString(),
        },
      ],
    };
    return {
      kind: 'answered',
      operation_uuid: record.operation_uuid,
      activation_state: record.activation_state,
      transaction_phase: record.transaction_phase,
      detail: record.detail,
      resident_session_changed: false,
      host_after_running: false,
    };
  }

  async signatureCheck(): Promise<SignatureCheckResult> {
    const kind = this.spec?.signature;
    // No robot and no release, so no verifier to run. Said, never drawn as a check with nothing in it.
    if (kind === undefined) return { ok: false, reason_code: 'this is the simulation inside the page: it holds no release to verify', detail: null };
    // A SCENARIO'S SIGNATURE CHECK: one run on record, in the runtime's shape, of an earlier runtime session (as 8771
    // served on 2026-09-27) or of this one. The capsules are the rehearsal's own. The Run button stays off: this source
    // runs no verifier (off_reason synthetic_source).
    if (kind === 'this-session' && this.signatureAt === null) this.signatureAt = new Date().toISOString();
    const row = (capsule: string, key: 'deployment' | 'throwaway'): Record<string, unknown> => ({
      capsule: capsule.replace(/^sha256:/, ''),
      kind: 'signed_capsule',
      key,
      expected: key === 'deployment' ? 'admitted' : 'refused',
      expected_reason: key === 'deployment' ? null : 'signature_key_id_not_trusted',
      observed: key === 'deployment' ? 'admitted' : 'refused',
      reason_code: key === 'deployment' ? null : 'signature_key_id_not_trusted',
      admitted: key === 'deployment',
      as_expected: true,
    });
    const state = parseSignatureCheckState({
      ok: true,
      signature_check_version: '1',
      in_flight: false,
      off_reason: 'synthetic_source',
      policy: 'require',
      checks: [
        {
          check_uuid: 'synthetic-signature-check-0001',
          runtime_session: kind === 'earlier-session' ? EARLIER_RUNTIME_SESSION : SYNTHETIC_SESSION,
          t_wall_utc: kind === 'earlier-session' ? '2026-09-26T20:26:13.435Z' : this.signatureAt,
          policy: 'require',
          trusted_keys: ['synthetic-deployment-key'],
          throwaway_public_key: null,
          capsules: 2,
          unsigned_twins: 1,
          rows: [
            row(MOBILE_CAPSULE, 'deployment'),
            row(MOBILE_CAPSULE, 'throwaway'),
            row(INERT_CAPSULE, 'deployment'),
            row(INERT_CAPSULE, 'throwaway'),
            { capsule: null, kind: 'unsigned_twin', key: 'deployment', expected: 'refused', expected_reason: 'signature_missing', observed: 'skipped', reason_code: 'unsigned_twin_not_staged', admitted: null, as_expected: null },
          ],
          rows_as_expected: 4,
          rows_not_as_expected: 0,
          rows_skipped: 1,
          rows_error: 0,
          as_expected: true,
          complete: false,
          duration_ms: 49,
        },
      ],
      checks_dropped: 0,
    });
    if (state === null) return { ok: false, reason_code: 'signature_check_unreadable', detail: null };
    return { ok: true, state };
  }

  async runSignatureCheck(): Promise<SignatureCheckOutcome> {
    // Refused, not unknown: this source is in the page and certainly ran no verifier.
    return { kind: 'refused', reason_code: 'this is the simulation inside the page: it runs no verifier', detail: null };
  }

  async historyEvents(runId: string): Promise<HistoryEventsResult> {
    // A RUN IN PROGRESS IS SERVED TOO, as far as it has got, as the runtime serves it from its log (`daemon.py`
    // `_op_history_events`, `journal.py` `read_run`): a tab opened mid-run reads its earlier records here.
    const events = this.finishedEvents[runId] ?? (this.run?.run_id === runId ? [...this.currentEvents] : undefined);
    if (events === undefined || events.length === 0) return { ok: false, reason_code: 'this source holds no records for that run' };
    // This source keeps no per-record origin: it IS the producer of every record it serves, and inventing an origin
    // list here would put a provenance on a replay that nothing recorded.
    return { ok: true, run_id: runId, events, origins: [] };
  }
}
