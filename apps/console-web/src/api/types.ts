// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// What the console asks of a data source, and the runtime surfaces it reads at that boundary.
//
// The shapes this file used to restate by hand, field for field, are now DERIVED from the generated contract types:
// the telemetry tap from `TapSlot`, the preview from `PreviewSlot`, the finished-run row from `HistoryEntry`, the
// block from `SnapshotUnresolvedCommand`, and the operator's statement IS `SnapshotAttestation` with nothing added.
// Each derivation states its own loosenings and nothing else, so a field the contract gains appears here without
// anybody noticing it was missing, and a field the contract does not have cannot be invented here.
//
// The loosenings are the same two facts every time. A runtime older than a field does not send it, so a field this
// console does not need is optional and an absent one reads as "the server did not say", never as a zero. And a TOKEN
// this console does not know is shown as itself rather than dropped, so where the contract has an enum for a value
// that only travels to the screen, the surface keeps `string`.
//
// ONE contract shape in this application is still written by hand, and it is not here: `contracts/envelope.ts`. The
// generator prints the event's `oneOf` branches and none of the envelope around them, so there is nothing to import
// for it. Everything else that describes a record or a runtime surface starts from the generated module.

import type {
  Command,
  CommandResult,
  HistoryEntry as ContractHistoryEntry,
  PreviewSlot,
  Snapshot,
  SnapshotAttestation,
  SnapshotUnresolvedCommand,
  SourceClockDomains,
  SourceState,
  TapSlot,
} from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import type { ServedCredential } from './boot';
import type { CapabilityCheckState } from '../model/capabilityCheck';
import type { SignatureCheckState } from '../model/signatureCheck';
import type { MissionCandidate, MissionRecord } from '../model/missionRecord';
import type { VlaStatus } from '../model/vla';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';

/** A read of a stored observation: its data, or why this page holds none (redesign r4 round 7 item 1). */
/** The operations the console's VLA door forwards (console_api/vla.py OPS, minus status). */
export type VlaOp = 'swap' | 'unswap' | 'run' | 'stop' | 'to_mission_pose' | 'carry';

export type ObservationRead<T> = { data: T; reason: null } | { data: null; reason: string };
import type { HostInstance, RobotStatus } from '../model/robotStatus';

export interface HelloResult {
  ok: boolean;
  contract_version: string | null;
  runtime_session: string | null;
  reason_code: string | null;
  /** The server may forbid commanding for its own reasons: a replay-only runtime, no command path, no runtime. */
  read_only: boolean;
  commands_enabled: boolean;
  /**
   * Who owns the credential this page was served with: `runtime_session` when it belongs to the runtime and survives a
   * restart of the API, `api_process` when the API had to mint one because no runtime had named a session yet. Null
   * when the server did not say, which is a server that is not this one and is never read as either answer.
   */
  credential_owner: string | null;
  /**
   * The runtime session the credential this page was served with belongs to, as the API names it
   * (`bound_runtime_session`). It is what a handshake the runtime did not answer can still say about whose runtime this
   * tab may command: a runtime that comes back under ANOTHER session has replaced it. Absent or null when not stated.
   */
  bound_runtime_session?: string | null;
}

/**
 * What the runtime's source supervisor says a source is doing. Only `connected` ever carries values; every other state
 * carries a reason and nulls, and this console prints the word unavailable rather than the last number it saw.
 *
 * These are the RUNTIME's own words, now stated once in `source.schema.json` and re-exported here so that the rest of
 * the application keeps one name for them.
 */
export type { SourceState };

/** The two clocks a slot spans, named by the runtime so that neither is ever subtracted from the other. */
export type ClockDomains = SourceClockDomains;

/**
 * One runtime SLOT as this console reads it off the wire: the fields the runtime has always published, plus every
 * other field of the contract's slot as one that may be absent or null.
 *
 * The slot is a runtime surface and not a recorded record, so it is read at the boundary rather than validated: a
 * runtime older than a field simply does not send it, and reading an absent number as a zero would put a measurement
 * on the screen that nobody took.
 */
type AsPublished<T, Always extends keyof T> = Pick<T, Always> & { [K in Exclude<keyof T, Always>]?: T[K] | null };

/**
 * Latest-only telemetry, derived from the contract's `TapSlot`. A slow reader is dropped by the runtime; it never
 * back-pressures anything.
 *
 * Two fields are deliberately looser than the contract's. `joints` is read as a map of joint NAME to value, because
 * this console prints whatever joints arrived rather than the six the contract names, and a host that reports another
 * one must not vanish from the table. `state` keeps `string`, because a state word this console does not know is
 * reported as the word it arrived as and never as a guess at the nearest one it does know.
 */
export type TapSample = AsPublished<
  Omit<TapSlot, 'joints' | 'state'>,
  'source_stamp' | 'base_velocity' | 'receive_age_ms' | 'subscriber_gap_count' | 'publisher_drop_count'
> & {
  joints: Record<string, number | null> | null;
  state?: SourceState | string | null;
};

/**
 * One camera preview slot, derived from the contract's `PreviewSlot`. `capture_age_ms` is null and `capture_age_reason`
 * says why: the capture stamp and this laptop's clock have no known relation, so an age across them would be a number
 * nobody measured, and `clock_relation` is the only thing that would ever license one. `service_latency_ms` is the one
 * span that IS inside a single clock, capture to dequeue on the host.
 */
export type PreviewSample = AsPublished<
  Omit<PreviewSlot, 'state'>,
  'role' | 'seq' | 'capture_clock' | 'receive_age_ms' | 'measured_fps' | 'available' | 'reason'
> & { state?: SourceState | string | null };

/** The camera roles this console knows, in the order it reads them. */
export const PREVIEW_ROLES = ['wrist', 'front'] as const;
export type PreviewRole = (typeof PREVIEW_ROLES)[number];

/**
 * A telemetry line. The runtime publishes ONE TOPIC PER CAMERA (`preview:wrist`, `preview:front`), so a line names the
 * camera it is about and a reader can never attribute one camera's frame to the other. The older single `preview` line
 * is still accepted from a runtime that publishes one stream, and then the record's own `role` says whose it is.
 */
export type TelemetryMessage =
  | { tap: TapSample }
  | { preview: PreviewSample }
  | { 'preview:wrist': PreviewSample }
  | { 'preview:front': PreviewSample };

/** The role a telemetry line is about, or null when the line is not a preview line. */
export function previewRoleOf(message: TelemetryMessage, record: PreviewSample | null): PreviewRole | null {
  for (const role of PREVIEW_ROLES) if (`preview:${role}` in message) return role;
  if (!('preview' in message)) return null;
  const named = record?.role;
  return (PREVIEW_ROLES as readonly string[]).includes(named ?? '') ? (named as PreviewRole) : 'wrist';
}

/** The preview record on a line, whichever topic carried it. */
export function previewSampleOf(message: TelemetryMessage): PreviewSample | null {
  for (const role of PREVIEW_ROLES) {
    const keyed = (message as Record<string, unknown>)[`preview:${role}`];
    if (keyed !== undefined) return keyed as PreviewSample;
  }
  return 'preview' in message ? message.preview : null;
}

/**
 * One finished run, derived from the contract's `HistoryEntry`. The browser used to keep five of the eleven fields the
 * runtime already sends; it now keeps them all, so a reader can tell a synthetic pass from a physical one without
 * opening the run.
 *
 * Four fields are looser than the contract's, each for a reason about a LIST the runtime folds from records that may
 * be older than the field: `command_id` and `adopted` are null on a row whose completion record carried neither, and
 * `command_type` and `outcome` keep `string` so a token from a newer runtime reaches the screen as itself instead of
 * being dropped by this console's copy of an enum.
 */
export type HistoryEntry = Omit<ContractHistoryEntry, 'command_id' | 'command_type' | 'outcome' | 'adopted'> & {
  command_id: string | null;
  command_type: string | null;
  outcome: string | null;
  adopted: boolean | null;
};

/**
 * An events line: an announced gap, one contract event, a drop notice or a line that could not be read. A gap is never
 * silently skipped.
 *
 * `missed` is NULL for the runtime's `cursor_ahead` resync, which is what it answers a cursor from a log it is not
 * serving any more: a replaced runtime session. There is no missing RANGE to state in that case, because the cursor
 * belongs to another sequence space altogether, and reading the absent range as a malformed line (which this parser
 * did) left a reconnecting tab counting parse failures instead of resynchronizing.
 */
export type EventsMessage =
  | { resync: { retained_from_seq: number; missed: [number, number] | null; reason: string | null } }
  | { event: ContractEvent }
  /** The server telling a reader it dropped records for it. It is a notice, not a malformed line. */
  | { dropped: unknown }
  | { malformed: unknown };

/**
 * What the runtime found when it looked at the world again for one command whose execution is NOT known. Everything in
 * here was read by the daemon on the machine the controller runs on; nothing in it is this console's opinion.
 *
 * `still_unknown` is not a failure of the request: it means a check could not be read, so nothing is concluded and the
 * block stays. A classification is never inferred here from the other fields.
 */
export type EvidenceClassification = 'ran_and_finished' | 'ran_and_was_interrupted' | 'never_started' | 'still_unknown';

/**
 * One statement an operator made about an uncertain command, recorded beside the machine evidence and never instead of
 * it. The contract's own object, field for field: this console restates none of it.
 */
export type Attestation = SnapshotAttestation;

/**
 * The snapshot's `unresolved_command`: the one command that holds the block, with the machine evidence behind it.
 *
 * It used to travel BESIDE the contract object, because the snapshot refuses unknown keys and no round owned that
 * file; it is now an optional property of the snapshot itself and this type is derived from the contract's. It is
 * still read defensively at the boundary, because the console does not revalidate what the runtime already validated
 * and a malformed one must be treated as "there is a block and this console cannot describe it", never as no block.
 *
 * Three fields are looser than the contract's: `command_type` and `blocked_commands` keep `string`, so a command type
 * from a newer runtime is shown to the operator as itself rather than being filtered out of the list of what is
 * blocked; and `evidence` is the readable map the screen flattens, which is the same object the contract leaves
 * undeclared on purpose.
 */
export type UnresolvedCommand = Omit<SnapshotUnresolvedCommand, 'command_type' | 'blocked_commands' | 'evidence'> & {
  command_type: string | null;
  blocked_commands: string[];
  /** The machine evidence, as the daemon read it. Held whole so the screen can show what was actually checked. */
  evidence: Record<string, unknown> | null;
};

/** What a resolve attempt came back with. `cleared` is the runtime's decision, never this console's. */
export type ResolutionOutcome =
  | {
      kind: 'answered';
      cleared: boolean;
      cleared_by: string | null;
      classification: EvidenceClassification | string | null;
      reason_code: string | null;
      evidence: Record<string, unknown> | null;
      attestation: Attestation | null;
      /** Present when the runtime refused the statement because it contradicts what it found. */
      contradiction: Record<string, unknown> | null;
    }
  /** The request did not get an answer this console could read. Nothing is assumed to have changed. */
  | { kind: 'unknown'; detail: string };

export type SubmitOutcome =
  | { kind: 'result'; result: CommandResult }
  /**
   * The response was lost AND the follow-up resolve did not answer. The command is NOT resent: the console says the
   * outcome is unknown and offers to resolve it again. This is the only honest answer to a lost response.
   */
  | { kind: 'unresolved'; command_id: string; detail: string };

/**
 * THE MISSION'S PART OF A STOP, as the runtime answered it beside the run's part.
 *
 * The runtime's one stop path stops the controller run first and then the mission, and says what it did to each. This
 * is the mission's half: which mission, whether its stop is now asked for (`stopping`), the state it had folded to when
 * the runtime answered, and whether a thread was carrying it. It is a REQUEST's answer. `stopping` true means the stop
 * was asked for and written down; it does not mean the mission has ended, and nothing here says that motion ceased.
 * `reason_code` is `no_running_mission` when there was no mission to stop, which is an answer and not a failure.
 */
export interface StopMissionPart {
  mission_id: string | null;
  stopping: boolean;
  reason_code: string | null;
  state: string | null;
  carried: boolean | null;
}

/**
 * What became of a stop REQUEST. This is not the stop itself: the contract's `stop.requested`, `stop.acknowledged` and
 * `stop.outcome` records are what say whether motion ceased. This says only whether the request was taken at all, and
 * a request that was refused or never landed is never allowed to look like silence.
 */
export interface StopAck {
  /** True only when the runtime said it took the request. */
  ok: boolean;
  /**
   * The id this console minted for THIS stop, always, including for a request whose fate is unknown. It is what the
   * runtime journaled the stop under, so a lost answer is asked about by id instead of the button being pressed again.
   */
  command_id: string;
  /** The runtime's receipt for that id, when the answer carried one. Null on the older un-journaled stop route. */
  result: CommandResult | null;
  requested_mono_ns: number | null;
  run_id: string | null;
  /** Why not, when not: the server's own reason_code, or this console's sentence for a request that never landed. */
  reason: string | null;
  /** `unknown` means the request's fate is not known, which is not the same as refused. */
  outcome: 'taken' | 'refused' | 'unknown';
  /**
   * The mission's part of the same stop, or null when the answer carried none: a runtime with no mission surface, an
   * older runtime, or a request whose answer never arrived. A stop that ended a mission while no controller run was
   * active is TAKEN on the strength of this part, and is never reported as refused.
   */
  mission: StopMissionPart | null;
  /**
   * WHAT THIS DISPLAY DID AFTER THE CONSOLE SERVER REFUSED ITS CREDENTIAL `credential_superseded` (redesign r4 verify-r4
   * must_fix 4), or absent/null when it did not. The fields above are then the answer to the SECOND Stop when one was
   * sent, else the first refusal's.
   */
  takeover?: StopTakeover | null;
}

/**
 * ONE PRESS OF STOP, SENT A SECOND TIME WITH THE CREDENTIAL OF THE PAGE THE CONSOLE SERVER SERVES NOW. The server
 * rotates its credential when any client makes it follow another runtime session, and this tab learns of that only from
 * its own reads; what "Reload to stop" asks a human to do (read the page again, press Stop there) the tab does itself,
 * once, for that Stop. The credential is used for that one request and kept nowhere.
 */
export interface StopTakeover {
  /** The id of the press's first Stop, the one the console server refused before it reached the runtime. */
  refused_command_id: string;
  /** True when the second Stop was sent; the ack's other fields are its answer. */
  sent: boolean;
  /** Why no second Stop was sent, in this display's words or the server's code; null when it was sent. */
  not_sent_reason: string | null;
  /** The runtime session the page's boot element names for its credential, when it names one. */
  runtime_session: string | null;
}

/**
 * What became of ONE request to start a mission, from the point of view of the request.
 *
 * The same three cases the activation keeps apart. `refused` is the API's guard or the runtime answering that it did not
 * start one, with its own reason code and, where it gave one, its detail (a runtime with no mission surface answers
 * `mission_unavailable` and says which of its requirements is missing). `unknown` is a request whose fate this console
 * cannot read: the mission record, read back, is what says whether a mission started, and nothing is sent again on it.
 * `started` carries the id the runtime wrote the mission down under; that it is running is read back, not assumed.
 */
export type MissionStartOutcome =
  | { kind: 'started'; mission_id: string | null }
  | { kind: 'refused'; reason_code: string; detail: string | null }
  | { kind: 'unknown'; detail: string };

/**
 * What became of ONE request to stop a mission through the mission's own door (POST /api/mission/stop). The answer is
 * the mission's part, with the run's stop beside it; `refused` and `unknown` mean what they mean everywhere else here.
 */
export type MissionStopOutcome =
  | { kind: 'answered'; mission: StopMissionPart; run_stopped: boolean | null }
  | { kind: 'refused'; reason_code: string; detail: string | null }
  | { kind: 'unknown'; detail: string };

/**
 * The finished-run list. A list that could not be read is NOT an empty list: "the runtime did not serve it" and "no run
 * has finished" are different facts and the screen says which one it has.
 */
export type HistoryResult = { ok: true; entries: HistoryEntry[] } | { ok: false; reason_code: string };

/**
 * Where one recorded record came from, beside the record itself: the producing session and that producer's OWN
 * sequence number, which the contract envelope has no field for. `seq` is the MISSION sequence the record was given
 * when the runtime logged it, and it is the only thing these may be paired with: the runtime sends an origin only for
 * the records that have one, so the list is not parallel to the records and is never read by position.
 */
export interface RecordOrigin {
  seq: number;
  source_session: string | null;
  source_seq: number | null;
}

/** The recorded events of one run. An empty or unreadable answer is a failure, never a run in which nothing happened. */
export type HistoryEventsResult =
  | { ok: true; run_id: string; events: ContractEvent[]; origins: RecordOrigin[] }
  | { ok: false; reason_code: string };

export type Unsubscribe = () => void;

export interface ConsoleSource {
  readonly kind: 'live' | 'synthetic';
  /** What the badge says. The source declares it; the snapshot confirms it. */
  readonly label: string;
  /**
   * Whose credential this page was served, as the console API's boot block said (`api/boot.ts`). Absent or null for a
   * source that was served no credential, and for an API that did not say.
   */
  readonly servedCredential?: ServedCredential | null;
  hello(): Promise<HelloResult>;
  snapshot(): Promise<Snapshot>;
  subscribeEvents(since: number, onMessage: (message: EventsMessage) => void, onReconnect: () => void): Unsubscribe;
  subscribeTelemetry(onMessage: (message: TelemetryMessage) => void): Unsubscribe;
  submit(commandId: string, command: Command): Promise<SubmitOutcome>;
  /** Ask the runtime what became of a command, by id. A READ: it never resends and never states anything. */
  resolve(commandId: string): Promise<CommandResult | null>;
  /**
   * Put the uncertain command's question back to the runtime with the operator's declared finding. This is a
   * STATEMENT, not a retry: the runtime re-reads its own evidence, records the finding as an attestation beside it and
   * decides for itself whether the block lifts. Nothing here ever resubmits the command.
   */
  resolveUncertain(commandId: string, finding: string, note: string | null): Promise<ResolutionOutcome>;
  stop(): Promise<StopAck>;
  /**
   * One stored observation's record or perception. `reason` says why no data is in hand, in this page's words around
   * the answer's own reason_code; it is null exactly when `data` is not.
   */
  observationRecord(observationId: string): Promise<ObservationRead<ObservationRecordDto>>;
  observationPerception(observationId: string): Promise<ObservationRead<PerceptionDto>>;
  /** The URL of an immutable stored frame, or null when this source has no image for it. */
  frameUrl(observationId: string, what: 'raw' | 'overlay', maxWidth: number): string | null;
  /** The URL of the live preview stream, or null when this source has none. */
  previewUrl(role: string): string | null;
  history(): Promise<HistoryResult>;
  historyEvents(runId: string): Promise<HistoryEventsResult>;
  /**
   * The controller's OWN stdout or stderr for one run, as the runtime serves it: bounded, redacted and with the size
   * of the whole file beside the part that was read. The run id is the only thing named; the runtime composes the
   * path. A source that has no such record answers with a reason rather than an empty log.
   */
  runOutput(runId: string, stream: 'stdout' | 'stderr'): Promise<RunOutputResult>;
  /**
   * What a real Cake resident answered, from the runtime's separately versioned read-only status surface.
   *
   * It is NOT part of the snapshot: the contract's `cake` object is sealed, and the structured facts a screen needs
   * would otherwise have to be scraped out of the ladder's evidence sentences, which are written for an operator to
   * read and are not a data format. A source with nothing to say answers with a reason rather than an empty resident.
   */
  cakeStatus(): Promise<CakeStatusResult>;
  /**
   * The mission record: the targets, where each one has got to, and every activation the runtime recorded.
   *
   * A READ, and the durable one. Everything the screen says about a mission comes from here, which is what makes a
   * reload recover it: nothing about a mission is kept in this browser. A source with no mission answers with a reason
   * rather than an empty mission, because "there is no mission" and "a mission with nothing in it" are different facts.
   */
  mission(): Promise<MissionResult>;
  /** The prepared candidates the runtime offers, by opaque key. A READ; it prepares nothing and activates nothing. */
  missionCandidates(): Promise<MissionCandidatesResult>;
  /**
   * Ask the runtime to activate ONE prepared candidate, BY KEY.
   *
   * The key is the only thing that travels. No bytes, no path and no operation name: what the key stands for is the
   * runtime's business, and a console that sent any of those would be choosing what the resident admits.
   *
   * The answer here is a receipt for the request and is never the verdict. The verdict is the activation the runtime
   * records in the mission, which is read back through `mission()` and is the thing that survives a reload.
   */
  activateCandidate(candidateKey: string): Promise<ActivationOutcome>;
  /**
   * START THE MISSION: POST /api/mission/start. It is the operator-facing Start, and the canonical way a mission begins.
   *
   * The body names no targets, so the runtime starts its own mission; nothing in it can name a file, a path or a
   * capability. It passes the same guard every command does. A runtime with no mission surface refuses it with its own
   * reason, and a LIVE runtime refuses it until a real execution adapter exists. The answer is a receipt; whether the
   * mission is running is read back from `mission()`, and a request whose answer was lost is never sent again.
   */
  missionStart(): Promise<MissionStartOutcome>;
  /**
   * TRY AGAIN after a failure the mission holds for the operator: POST /api/mission/retry. Optional: a source that has
   * no such door leaves it out, and the screen then says the runtime does not offer a retry instead of drawing a button
   * that would be refused. The body names the mission whose hold the operator saw, so a press meant for one mission can
   * never retry another. The answer is a receipt; the mission record says whether an attempt started.
   */
  missionRetry?(missionId: string): Promise<MissionStartOutcome>;
  /** GET /api/vla/status: the robot-side VLA runner's state, or unreachable. Absent on sources with no VLA door. */
  vlaStatus?(): Promise<VlaStatus>;
  /** POST /api/vla/{op}: forwarded to the robot-side runner, which decides. The answer is a receipt; the status says what happened. */
  vlaCommand?(op: VlaOp): Promise<{ ok: boolean; answer: unknown }>;
  /**
   * STOP THE MISSION THROUGH ITS OWN DOOR: POST /api/mission/stop. The runtime stops the controller run first through
   * its guarded path and then the mission, exactly as the unified POST /api/stop does; the header's Stop uses that one,
   * because it also carries a command id the runtime journals. Neither ever signals the robot host or the resident.
   */
  missionStop(): Promise<MissionStopOutcome>;
  /**
   * The ROBOT's Cake resident, as the runtime reaches it: GET /api/robot. A READ; it grants nothing and starts nothing.
   * A source with no robot answers with a reason rather than an invented robot.
   */
  robotStatus(): Promise<RobotStatusResult>;
  /**
   * The operator confirms the connected robot: the resident session and the supervised HOST INSTANCE (slot instance
   * handle, spawn sequence, pid) EXACTLY as the screen displayed them. It GRANTS permission to move, so it goes through
   * the same guarded path and headers every command does, and the runtime refuses it unless every component matches a
   * fresh read of its own. It is never sent on a timer or a load.
   */
  confirmRobotSession(sessionUuid: string, hostInstance: HostInstance): Promise<RobotConfirmOutcome>;
  /**
   * THE CAPABILITY SLOT CHECK'S READ: GET /api/capability/check. It submits nothing. A runtime with no check (not LIVE,
   * or no prepared Plan) answers with a reason and a detail, which is not an empty check.
   */
  capabilityCheck(): Promise<CapabilityCheckResult>;
  /**
   * APPLY ONE PREPARED PLAN to the robot's capability slot and read it back: POST /api/capability/check. NO MOTION, and
   * never for a mission. The body is exactly the key, the digest shown beside it and the resident session the current
   * reading shows. The answer is a receipt; the record is read back through `capabilityCheck()`, and a request whose
   * answer was lost is never sent again.
   */
  applyCapabilityCheck(candidateKey: string, payloadDigest: string, sessionUuid: string): Promise<CapabilityCheckOutcome>;
  /**
   * THE SIGNATURE POLICY CHECK'S READ: GET /api/capability/signature. It runs nothing. A runtime with no check (not LIVE,
   * not on the robot, or naming no release) answers with a reason and a detail, which is not an empty check.
   */
  signatureCheck(): Promise<SignatureCheckResult>;
  /**
   * RUN THE RELEASE VERIFIER on the robot over every deployed capsule: POST /api/capability/signature. NO MOTION, no slot
   * touched. The body is EMPTY: the verifier, the store, the keys and the policy are the deployed release's own. The
   * answer is a receipt; the record is read back through `signatureCheck()`, and a request whose answer was lost is
   * never sent again.
   */
  runSignatureCheck(): Promise<SignatureCheckOutcome>;
}

/** What one read of the signature policy check came back as. */
export type SignatureCheckResult = { ok: true; state: SignatureCheckState } | { ok: false; reason_code: string; detail: string | null };

/**
 * What became of ONE signature check request, from the point of view of the request: refused before anything ran,
 * answered by the runtime with its counts, or a fate this console cannot read, which is never resent.
 */
export type SignatureCheckOutcome =
  | {
      kind: 'answered';
      check_uuid: string | null;
      as_expected: boolean | null;
      complete: boolean | null;
      rows_as_expected: number;
      rows_not_as_expected: number;
      rows_skipped: number;
      rows_error: number;
    }
  | { kind: 'refused'; reason_code: string; detail: string | null }
  | { kind: 'unknown'; detail: string };

/** What one read of the capability slot check came back as. */
export type CapabilityCheckResult = { ok: true; state: CapabilityCheckState } | { ok: false; reason_code: string; detail: string | null };

/**
 * What became of ONE check request, from the point of view of the request: refused before anything was submitted,
 * answered by the runtime, or a fate this console cannot read, which is never resent.
 */
export type CapabilityCheckOutcome =
  | {
      kind: 'answered';
      operation_uuid: string | null;
      activation_state: string;
      transaction_phase: string | null;
      detail: string | null;
      resident_session_changed: boolean | null;
      host_after_running: boolean | null;
    }
  | { kind: 'refused'; reason_code: string; detail: string | null }
  | { kind: 'unknown'; detail: string };

/** What one read of the robot status surface came back as. */
export type RobotStatusResult = { ok: true; status: RobotStatus } | { ok: false; reason_code: string };

/**
 * What became of ONE confirmation, from the point of view of the request. The same three cases the activation keeps
 * apart: refused before the runtime, answered by it, or a fate this console cannot read, which is never resent.
 */
export type RobotConfirmOutcome =
  | {
      kind: 'answered';
      ok: boolean;
      reason_code: string | null;
      confirmed_session_uuid: string | null;
      confirmed_host_instance: HostInstance | null;
      confirmed_wall_utc: string | null;
      motion_permitted: boolean | null;
    }
  | { kind: 'refused'; reason_code: string }
  | { kind: 'unknown'; detail: string };

/**
 * What one read of the mission surface came back as.
 *
 * A refusal carries the runtime's `detail` where it sent one. It matters for one answer above all: `mission_unavailable`
 * is a runtime with NO mission surface, and its detail says which requirement is missing (a live runtime says
 * `rehearsal_requires_synthetic_mode`, because the only execution adapter it has is the rehearsal's). That is the reason
 * the Start control is off, and it is shown as the runtime's own words rather than guessed at. `no_mission_recorded` is
 * a different fact: the surface is there and nothing has been started on it yet.
 */
export type MissionResult = { ok: true; mission: MissionRecord } | { ok: false; reason_code: string; detail?: string | null };

/** What one read of the candidate list came back as. An empty list is a list; a failure is a reason. */
export type MissionCandidatesResult = { ok: true; candidates: MissionCandidate[] } | { ok: false; reason_code: string };

/**
 * What became of ONE activation request, from the point of view of the request.
 *
 * The three cases are the same three the command path already keeps apart, for the same reason. `refused` is the API's
 * guard answering before the runtime was touched, so the request did not land. `unknown` is a request whose fate this
 * console cannot read: it is NOT reported as "did not land", because that would state the one thing nobody knows, and
 * nothing is ever sent a second time on it. `answered` carries the runtime's own receipt.
 */
export type ActivationOutcome =
  | {
      kind: 'answered';
      operation_uuid: string | null;
      activation_state: string;
      outcome: string | null;
      transaction_phase: string | null;
      detail: string | null;
    }
  | { kind: 'refused'; reason_code: string }
  | { kind: 'unknown'; detail: string };

/** What one read of a run's controller output came back as. */
export type RunOutputResult =
  | { ok: true; run_id: string; stream: 'stdout' | 'stderr'; text: string; total_bytes: number; truncated: boolean }
  | { ok: false; reason_code: string };

/** The identity the resident printed for itself. Every field is null when it printed nothing this console recognised. */
export interface CakeResidentIdentity {
  session_uuid: string | null;
  build_identity: string | null;
  config_identity: string | null;
  target_profile_digest: string | null;
  /** Does NOT establish that any particular Plan was admitted, and must never be drawn as if it did. */
  plan_digest: string | null;
}

/** What the resident reports it holds. Zero is its own answer; null is the absence of one. */
export interface CakeResidentCounts {
  epoch: number | null;
  slots: number | null;
  bindings: number | null;
  resources: number | null;
  replay: number | null;
  snapshot_present: number | null;
}

/** One successful read of the resident, and when it was taken. */
export interface CakeObservation {
  /** For display only. Ages come from age_ms, which is measured on one monotonic clock. */
  wall_utc: string;
  mono_ns: number;
  /** How long ago this read was taken. It is what makes a retained observation legible as retained. */
  age_ms: number;
  resident: CakeResidentIdentity;
  counts: CakeResidentCounts;
  transaction_state: string | null;
  flight: { records: number | null; oldest: number | null; next: number | null } | null;
}

/** One claim of the runtime's and what it rests on. `value` null means it did not establish it, which is never a no. */
export interface CakeFinding {
  value: boolean | null;
  evidence: string;
}

/**
 * The Cake resident status, as the runtime reports it.
 *
 * THE THREE FACTS STAY APART. `reachable` says a resident answered. `activity` says whether anything is running in it,
 * and is null far more often than it is false. `mission_participation` says whether this robot's task is being carried
 * by Cake, and is not derived from either of the others.
 */
export interface CakeStatus {
  cake_status_version: string;
  /** Whether this runtime was told where a resident is. False means nothing was asked, not that asking failed. */
  configured: boolean;
  reachable: boolean;
  reason_code: string | null;
  /** True when `observation` is retained from a read that is no longer current. Draw it as old, never as an empty resident. */
  stale: boolean;
  /** True once a different resident session has answered. Null until the runtime has had two answers to compare. */
  session_changed: boolean | null;
  clock_domain: string;
  /** The most recent read that SUCCEEDED, or null when none ever has. Never an inventory of zeroes standing in for one. */
  observation: CakeObservation | null;
  activity: CakeFinding;
  mission_participation: CakeFinding;
}

/** What one read of the Cake status surface came back as. */
export type CakeStatusResult = { ok: true; status: CakeStatus } | { ok: false; reason_code: string };
