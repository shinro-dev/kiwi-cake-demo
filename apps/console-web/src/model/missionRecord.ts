// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MISSION RECORD, as the runtime serves it, read at the boundary.
//
// A mission carries more than one target and outlives a single run: the two cubes are carried one after the other, and
// between them the mission can be HELD because a capability the next target needs is not in the resident. None of that
// is in the contract fold, which knows about runs and about the controller's states and about nothing above them. So
// this is a separate surface with its own record version, read the way `api/types.ts` reads every other runtime
// surface: defensively, field by field, with an absent field read as "the runtime did not say" and never as a zero, a
// false or an empty list of something that exists.
//
// A TOKEN THIS CONSOLE DOES NOT KNOW IS KEPT AS ITSELF. `state`, `activation_state`, `outcome` and `transaction_phase`
// are typed as the union this console has words for OR a bare string, so a newer runtime's word reaches the screen as
// the word it sent. Nothing here maps an unknown token onto the nearest known one: that is how a rejection comes to be
// drawn as a success.
//
// THIS FILE DECIDES NOTHING. It reads. What the activation states MEAN, and which of them may ever be drawn as an
// activation, is decided in `design/cake.ts`, and which target the rail describes is decided in `model/missionView.ts`.

/** What the mission as a whole is doing. */
export type MissionState = 'RUNNING' | 'WAITING_FOR_CAPABILITY' | 'COMPLETE' | 'STOPPED' | 'FAILED';

/**
 * Where one target has got to. A target is one cube. `STOPPED` is a target the mission was carrying, or waiting on, when
 * the mission was stopped: the runtime gives it that end rather than leaving it reading ACTIVE, which would draw a robot
 * still working on a cube nobody is carrying any more.
 */
export type MissionTargetState = 'PENDING' | 'ACTIVE' | 'PLACED' | 'WAITING_FOR_CAPABILITY' | 'FAILED' | 'STOPPED';

/**
 * What became of one prepared activation.
 *
 * The five are NOT a progression and must never be collapsed into one. `SUBMITTED` is an operation IN FLIGHT: the
 * attempt was written and either no answer has come back or the resident says the transaction is still running.
 * `REQUEST_FAILED` is a request that did not land, so nothing judged it; `REJECTED_BY_CAKE` is a transaction the resident
 * aborted; `COMPLETED_NOT_OBSERVED` is a transaction that completed while the slot, read back, does NOT carry the
 * capability; and `OBSERVED` is the slot read back afterwards carrying it, which is the only one of the five that means
 * the capability is there.
 *
 * `COMPLETED_NOT_OBSERVED` is newer than the other four. It used to be recorded as SUBMITTED, which also meant in
 * flight, so a reader could not tell an operation still running from one that had finished without moving the slot.
 */
export type ActivationState = 'SUBMITTED' | 'REJECTED_BY_CAKE' | 'REQUEST_FAILED' | 'COMPLETED_NOT_OBSERVED' | 'OBSERVED';

/**
 * ONE READ OF THE CAPABILITY SLOT, as the runtime recorded it beside an activation: once just before the submit
 * (`slot_before`) and once after the answer (`slot_after`). It is the evidence behind the verdict, kept so a reader can
 * see which resident answered, under which Plan and epoch, and whether the slot's capsule and generation moved.
 *
 * Every count (generation, instance, epoch) arrives as a DECIMAL STRING, because they are u64 values and a browser
 * cannot hold one as a number without rounding it. They are shown as the text they are and never parsed.
 *
 * `read` says how the read went: `ok` is a listing that answered and named the slot, `slot_absent` a listing that answered
 * without it, `failed` a read that produced no listing at all. A failed read is NOT an empty slot and is never drawn as
 * one. `capability_active` is the verdict of this same read; it is shown as the runtime's, and what may be drawn as an
 * activation is still decided in `design/cake.ts` and only there.
 */
export interface SlotReading {
  read: 'ok' | 'failed' | 'slot_absent' | string | null;
  slot_uuid: string | null;
  name: string | null;
  state: string | null;
  capsule: string | null;
  generation: string | null;
  instance: string | null;
  session_uuid: string | null;
  plan_digest: string | null;
  epoch: string | null;
  capability_active: boolean | null;
}

/**
 * What the resident's answer said about the operation, as the runtime recorded it. Carried for the record: the outcome
 * is `activation_state`, and nothing here is ever read as one. `answer_epoch` is null when the answer printed none,
 * which is an answer that did not say, not an epoch of zero.
 */
export interface ActivationTransaction {
  operation_uuid: string | null;
  transaction_uuid: string | null;
  transaction_handle: string | null;
  transaction_phase: string | null;
  status: string | null;
  reason_code: string | null;
  answer_epoch: string | null;
}

export interface MissionTargetRecord {
  target_id: string;
  label: string;
  /** Whether the mission established the target can be reached. Null is "not determined", which is not a no. */
  reachable: boolean | null;
  /**
   * WHETHER this target needs a capability before it can be carried. The runtime writes a BOOLEAN
   * (`demo_runtime/mission_service.py`, `requires_capability: bool(...)`), not a name: the capability's name is the
   * robot's configured one, read from GET /api/robot. It was parsed as text, which turned the runtime's `true` into
   * null, and the far cube, the one target that needs a capability, was drawn as needing none (design finding F1).
   * Null is "the record did not say", which is not a no.
   *
   * THE THIRD VALUE IS THE WORD `auto` (docs/autonomous-demo/one-block-demo-2026-09-26.md, T1): the target takes
   * whichever placement mode its own SEARCH measures, and whether it needs the capability is decided by that
   * measurement, which `measured_mode` carries once it was made. Exactly that word, in that case; any other text is
   * not a value this console knows and reads as not recorded, never as a declaration.
   */
  requires_capability: boolean | 'auto' | null;
  /**
   * THE PLACEMENT MODE THE ROBOT MEASURED for this target (`ARM_ONLY` or `BASE_ADVANCE`, as the controller names them),
   * or null while nothing has measured it. The runtime's own record carries the first execution's binding under
   * `binding.mode` (`demo_runtime/mission.py`, `mission_target_bound`); a target record may also carry it as
   * `measured_mode` or `mode`, and all three are read, in that order. A mode word this console does not know is kept as
   * the runtime wrote it. Null is "not measured", which is not a mode. Optional for the same reason the activation
   * additions are: a record built by hand in a test need not carry it, and `parseTarget` always sets it.
   */
  measured_mode?: string | null;
  state: MissionTargetState | string;
  /**
   * The runs the mission recorded AGAINST THIS TARGET, and against no other.
   *
   * It is the only thing that says which child run belongs to which cube. A run id that is in this list is this
   * target's; a run id that is not is somebody else's and is never drawn here.
   */
  runs: string[];
  /**
   * EVERY ATTEMPT THE RUNTIME WROTE DOWN FOR THIS TARGET (`mission_target_attempt`), oldest first: which run, what it came
   * to, where it left the robot and what a press can do after it. Optional because a record older than the field, or one
   * built by hand in a test, does not carry it; `parseTarget` always sets it, to an empty list when absent.
   */
  attempts?: MissionAttemptRecord[];
  /** The Retries asked for on this target (`retries`), each naming the attempt it followed. Optional, as `attempts`. */
  retries?: MissionRetryRecord[];
  /** The runs that parked the arm before a Retry (`park_runs`): runs of this target that are not attempts at the block. */
  park_runs?: string[];
}

/** Where an attempt left the robot, each part true, false or null for unknown. Null is never read as false. */
export interface MissionStopState {
  holding: boolean | null;
  base_ahead: boolean | null;
  moved: boolean | null;
}

/** One attempt at a target, as the runtime wrote it down after the attempt's run ended. */
export interface MissionAttemptRecord {
  attempt: number | null;
  run_id: string | null;
  park_run_id: string | null;
  /** The runtime's class of the attempt: `placed`, `retryable`, `operator_needed`, `base_only`, `fatal` or `stopped`. */
  outcome_class: string | null;
  reason_code: string | null;
  phase: string | null;
  measurements: Array<{ name: string; value: string | number | boolean | null; unit: string | null }>;
  observation_ids: string[];
  stop_state: MissionStopState | null;
  next_actions: string[];
  budget_left: number | null;
  t_wall_utc: string | null;
}

/** One Retry asked for, as the runtime recorded it. */
export interface MissionRetryRecord {
  attempt: number | null;
  origin: string | null;
  t_wall_utc: string | null;
}

export interface MissionActivationRecord {
  operation_uuid: string;
  target_id: string | null;
  /** The opaque key the operator chose from the runtime's own candidate list. Never a path and never bytes. */
  candidate_key: string | null;
  payload_digest: string | null;
  activation_state: ActivationState | string;
  /** The runtime's own word for the outcome, kept unaltered. It is not this console's verdict and never replaces one. */
  outcome: string | null;
  /** The phase the TRANSACTION reached. A phase is not an activation; see `design/cake.ts`. */
  transaction_phase: string | null;
  transaction_uuid: string | null;
  detail: string | null;
  t_wall_utc: string | null;
  /**
   * The three additions of the slot read-back, optional because a record written before them does not carry them and
   * a test that builds a record by hand need not. `parseActivation` always sets them, to null when absent, and absent
   * reads as "the record does not say", never as a slot that did not move.
   */
  slot_before?: SlotReading | null;
  slot_after?: SlotReading | null;
  transaction?: ActivationTransaction | null;
  /** Whether the mission had been asked to stop by the time the answer came back. Null when the record does not say. */
  stopped_during_activation?: boolean | null;
}

/**
 * THE HOLD AFTER A FAILED ATTEMPT, as the runtime serves it (`demo_runtime/mission.py`, `_hold_view`): present only
 * while the mission waits for the operator's decision, built from the held target's newest attempt record. Everything
 * in it was written down before the hold began.
 *
 * `next_actions` is what a press can do NOW, in the runtime's words: `retry` or `move_block_then_retry` only when the
 * runtime will carry a retry (the budget has one left, nothing is held, the base is home, and this runtime can park the
 * arm), then `stop`. A hold whose list holds only `stop` offers only Stop, and the screen never offers more.
 */
export interface MissionHold {
  target_id: string | null;
  attempt: number | null;
  run_id: string | null;
  park_run_id: string | null;
  /** The runtime's class of the attempt: `retryable` or `operator_needed` while it holds. */
  outcome_class: string | null;
  reason_code: string | null;
  phase: string | null;
  observation_ids: string[];
  /** Where the attempt left the robot, each true, false or null for unknown. */
  stop_state: { holding: boolean | null; base_ahead: boolean | null; moved: boolean | null } | null;
  next_actions: string[];
  budget_left: number | null;
  /** A retry was already asked for after this attempt. */
  retry_requested: boolean | null;
  /**
   * The named numbers that decided the failure and when the attempt was written, as the runtime serves them. Optional:
   * this console reads the failure's numbers from the run's own record, and a hold built by hand need not carry them.
   */
  measurements?: Array<{ name: string; value: string | number | boolean | null; unit: string | null }>;
  t_wall_utc?: string | null;
}

export interface MissionRecord {
  mission_record_version: string | null;
  mission_id: string;
  state: MissionState | string;
  data_mode: string | null;
  /**
   * What the EXECUTION ADAPTER said about itself when the mission started: whether it moves the robot. A rehearsal
   * says false. Null for a record written before the field existed, which is never read as true: a target's PLACED
   * state is a physical placement only when this is true AND `data_mode` is live (`model/missionView.ts`).
   */
  performs_motion: boolean | null;
  /** The adapter's own name for how it executes, for instance `recording-rehearsal`. Shown as written. */
  execution_label: string | null;
  started_wall_utc: string | null;
  /** The target the mission is on now, by id. Null when the mission names none. */
  selected_target: string | null;
  /** Why the mission is held, when it is. Today the runtime writes `capability_unavailable`. */
  waiting_reason: string | null;
  detail: string | null;
  /**
   * A stop that was ASKED FOR, and when. It is a request: `state` says whether the mission has ended, and nothing here
   * says that motion ceased. Optional for the same reason as the activation additions; parsed to false and null when
   * a record predates them.
   */
  stop_requested?: boolean;
  stop_requested_wall_utc?: string | null;
  /** Records that arrived after the mission ended and were not allowed to change it. Shown, never hidden. */
  records_after_end?: number | null;
  targets: MissionTargetRecord[];
  /** Every activation the runtime recorded, in the order it recorded them. It is the durable history. */
  activations: MissionActivationRecord[];
  /**
   * The hold the runtime serves while the mission waits for the operator, or null when it serves none. Optional for
   * the same reason as the activation additions; `parseMission` always sets it.
   */
  hold?: MissionHold | null;
  /**
   * What the operator can do now for the whole mission, in the runtime's words, or null when the record carries no
   * list (a runtime older than the field). Null is "not said", never an empty offer.
   */
  next_actions?: string[] | null;
}

/**
 * One prepared candidate the runtime offers. The key is opaque and is the ONLY thing a console ever sends back.
 *
 * `role`, `label` and `expect` are what the runtime's configuration says the candidate is for and which activation state
 * its press is expected to record (`cake.candidate_roles`). A runtime that configures none serves key and digest only,
 * and all three are then null: a candidate with no expectation is never given one here. Optional so a list built by
 * hand need not carry them; `parseCandidates` always sets them.
 */
export interface MissionCandidate {
  key: string;
  payload_digest: string | null;
  role?: string | null;
  label?: string | null;
  expect?: string | null;
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

/** The capability flag: a boolean, the one word `auto`, or absent. Never any other text, and never coerced. */
function asCapabilityFlag(value: unknown): boolean | 'auto' | null {
  return value === 'auto' ? 'auto' : asBool(value);
}

/** The measured placement mode, from the first of the three places a record may carry it. */
function asMeasuredMode(value: Record<string, unknown>): string | null {
  const own = asText(value['measured_mode']) ?? asText(value['mode']);
  if (own !== null) return own;
  const binding = value['binding'];
  return isRecord(binding) ? asText(binding['mode']) : null;
}

function asCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * A u64 count as the runtime wrote it: a decimal STRING. A number is accepted only when it is a safe integer, and is
 * turned into its text; anything else is absent. A count is never rounded into a different count on its way here.
 */
function asDecimal(value: unknown): string | null {
  if (typeof value === 'string') return /^[0-9]{1,20}$/.test(value) ? value : null;
  if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0) return String(value);
  return null;
}

/** One slot reading, or null when the record carries none. Each field is read as what it is, or absent. */
export function parseSlotReading(value: unknown): SlotReading | null {
  if (!isRecord(value)) return null;
  return {
    read: asText(value['read']),
    slot_uuid: asText(value['slot_uuid']),
    name: asText(value['name']),
    state: asText(value['state']),
    capsule: asText(value['capsule']),
    generation: asDecimal(value['generation']),
    instance: asDecimal(value['instance']),
    session_uuid: asText(value['session_uuid']),
    plan_digest: asText(value['plan_digest']),
    epoch: asDecimal(value['epoch']),
    capability_active: asBool(value['capability_active']),
  };
}

/** What the resident's answer said about the operation, or null when the record carries none. */
export function parseTransaction(value: unknown): ActivationTransaction | null {
  if (!isRecord(value)) return null;
  return {
    operation_uuid: asText(value['operation_uuid']),
    transaction_uuid: asText(value['transaction_uuid']),
    transaction_handle: asDecimal(value['transaction_handle']),
    transaction_phase: asText(value['transaction_phase']),
    status: asText(value['status']),
    reason_code: asText(value['reason_code']),
    answer_epoch: asDecimal(value['answer_epoch']),
  };
}

/** A list of strings, with anything that is not one left out rather than coerced into one. */
function asTextList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const item of value) {
    const text = asText(item);
    if (text !== null) out.push(text);
  }
  return out;
}

/**
 * One target. A row without an id is not a target: nothing could be attributed to it and its runs could not be kept
 * apart from another's, so it is dropped rather than shown under an invented name.
 */
export function parseTarget(value: unknown): MissionTargetRecord | null {
  if (!isRecord(value)) return null;
  const id = asText(value['target_id']);
  const state = asText(value['state']);
  if (id === null || state === null) return null;
  return {
    target_id: id,
    // A target the runtime did not name is shown by its id rather than by a label this console made up for it.
    label: asText(value['label']) ?? id,
    reachable: asBool(value['reachable']),
    requires_capability: asCapabilityFlag(value['requires_capability']),
    measured_mode: asMeasuredMode(value),
    state,
    runs: asTextList(value['runs']),
    attempts: asRecords(value['attempts']).map(parseAttempt),
    retries: asRecords(value['retries']).map((item) => ({ attempt: asCount(item['attempt']), origin: asText(item['origin']), t_wall_utc: asText(item['t_wall_utc']) })),
    park_runs: asTextList(value['park_runs']),
  };
}

function asTriState(value: unknown): { holding: boolean | null; base_ahead: boolean | null; moved: boolean | null } | null {
  if (!isRecord(value)) return null;
  return { holding: asBool(value['holding']), base_ahead: asBool(value['base_ahead']), moved: asBool(value['moved']) };
}

/** A recorded measurement's value as the record carries it: text, a finite number, a boolean, or null. */
function asMeasuredValue(value: unknown): string | number | boolean | null {
  if (typeof value === 'string' || typeof value === 'boolean') return value;
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asMeasurements(value: unknown): MissionAttemptRecord['measurements'] {
  if (!Array.isArray(value)) return [];
  const out: MissionAttemptRecord['measurements'] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const name = asText(item['name']);
    if (name === null) continue;
    out.push({ name, value: asMeasuredValue(item['value']), unit: asText(item['unit']) });
  }
  return out;
}

/** One attempt record, field by field. An absent field is null, never a zero or a false. */
export function parseAttempt(value: Record<string, unknown>): MissionAttemptRecord {
  return {
    attempt: asCount(value['attempt']),
    run_id: asText(value['run_id']),
    park_run_id: asText(value['park_run_id']),
    outcome_class: asText(value['outcome_class']),
    reason_code: asText(value['reason_code']),
    phase: asText(value['phase']),
    measurements: asMeasurements(value['measurements']),
    observation_ids: asTextList(value['observation_ids']),
    stop_state: asTriState(value['stop_state']),
    next_actions: asTextList(value['next_actions']),
    budget_left: asCount(value['budget_left']),
    t_wall_utc: asText(value['t_wall_utc']),
  };
}

function asRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

/** The served hold, or null when the record carries none. Its next actions are kept as the runtime wrote them. */
export function parseHold(value: unknown): MissionHold | null {
  if (!isRecord(value)) return null;
  return {
    target_id: asText(value['target_id']),
    attempt: asCount(value['attempt']),
    run_id: asText(value['run_id']),
    park_run_id: asText(value['park_run_id']),
    outcome_class: asText(value['outcome_class']),
    reason_code: asText(value['reason_code']),
    phase: asText(value['phase']),
    observation_ids: asTextList(value['observation_ids']),
    stop_state: asTriState(value['stop_state']),
    next_actions: asTextList(value['next_actions']),
    budget_left: asCount(value['budget_left']),
    retry_requested: asBool(value['retry_requested']),
  };
}

/** One activation. Without an operation id and a state there is nothing to report about it, and it is dropped. */
export function parseActivation(value: unknown): MissionActivationRecord | null {
  if (!isRecord(value)) return null;
  const uuid = asText(value['operation_uuid']);
  const state = asText(value['activation_state']);
  if (uuid === null || state === null) return null;
  return {
    operation_uuid: uuid,
    target_id: asText(value['target_id']),
    candidate_key: asText(value['candidate_key']),
    payload_digest: asText(value['payload_digest']),
    activation_state: state,
    outcome: asText(value['outcome']),
    transaction_phase: asText(value['transaction_phase']),
    transaction_uuid: asText(value['transaction_uuid']),
    detail: asText(value['detail']),
    t_wall_utc: asText(value['t_wall_utc']),
    slot_before: parseSlotReading(value['slot_before']),
    slot_after: parseSlotReading(value['slot_after']),
    transaction: parseTransaction(value['transaction']),
    stopped_during_activation: asBool(value['stopped_during_activation']),
  };
}

/**
 * The mission record, or null when the answer is not one.
 *
 * `mission_id` and `state` are what make it a mission at all; an answer without them is reported as unreadable rather
 * than drawn as a mission in an unknown state. Everything else is optional, because a runtime older than a field does
 * not send it.
 */
export function parseMission(value: unknown): MissionRecord | null {
  if (!isRecord(value)) return null;
  const id = asText(value['mission_id']);
  const state = asText(value['state']);
  if (id === null || state === null) return null;
  const targets: MissionTargetRecord[] = [];
  if (Array.isArray(value['targets'])) {
    for (const row of value['targets']) {
      const target = parseTarget(row);
      if (target !== null) targets.push(target);
    }
  }
  const activations: MissionActivationRecord[] = [];
  if (Array.isArray(value['activations'])) {
    for (const row of value['activations']) {
      const activation = parseActivation(row);
      if (activation !== null) activations.push(activation);
    }
  }
  return {
    mission_record_version: asText(value['mission_record_version']),
    mission_id: id,
    state,
    data_mode: asText(value['data_mode']),
    performs_motion: asBool(value['performs_motion']),
    execution_label: asText(value['execution_label']),
    started_wall_utc: asText(value['started_wall_utc']),
    selected_target: asText(value['selected_target']),
    waiting_reason: asText(value['waiting_reason']),
    detail: asText(value['detail']),
    // Only an explicit true is a stop that was asked for. A record that does not say did not say yes.
    stop_requested: value['stop_requested'] === true,
    stop_requested_wall_utc: asText(value['stop_requested_wall_utc']),
    records_after_end: asCount(value['records_after_end']),
    targets,
    activations,
    hold: parseHold(value['hold']),
    next_actions: Array.isArray(value['next_actions']) ? asTextList(value['next_actions']) : null,
  };
}

/** The candidate list. A row without a key cannot be activated and is not offered. */
export function parseCandidates(value: unknown): MissionCandidate[] {
  const rows = isRecord(value) && Array.isArray(value['candidates']) ? value['candidates'] : Array.isArray(value) ? value : null;
  if (rows === null) return [];
  const out: MissionCandidate[] = [];
  for (const row of rows) {
    if (!isRecord(row)) continue;
    const key = asText(row['key']);
    if (key === null) continue;
    out.push({ key, payload_digest: asText(row['payload_digest']), role: asText(row['role']), label: asText(row['label']), expect: asText(row['expect']) });
  }
  return out;
}
