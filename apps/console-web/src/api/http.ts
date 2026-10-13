// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The live source: the runtime's HTTP surface.
//
// Three rules shape this file:
//
//  1. A command is submitted ONCE. If the response is lost, the outcome is recovered with GET /api/commands/{id} using
//     the id the client minted before it sent anything. Nothing is resent, nothing is queued and nothing is retried on
//     its own, because a retried motion command is a second motion.
//  2. A REFUSAL IS NOT A LOST RESPONSE. The API's guard refuses a request before the runtime socket is touched at all,
//     and it says why. Calling that "the response was lost, whether it ran is not known" would be false in the one
//     direction that matters: it certainly did not run. A refusal is reported as a rejection with the server's own
//     reason_code; only a request whose fate is genuinely unknown goes down the resolve path.
//  3. Every mutating request carries what the guard requires: the json content type and the two headers from the boot
//     element. Without them the guard refuses, and the console shows that refusal rather than hiding it.

import type { Command, CommandResult, DataMode, Snapshot } from '@kiwi/demo-contracts';
import { isContractEvent } from '../contracts/envelope';
import { parseObservationRecord, parsePerception } from '../model/observation';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';
import type { ConsoleBoot, ConsoleCredential, ServedCredential } from './boot';
import { bootFromHtml, commandHeaders } from './boot';
import { mintCommandId } from './commandId';
import { parseCapabilityCheckState } from '../model/capabilityCheck';
import { parseSignatureCheckState } from '../model/signatureCheck';
import { parseCandidates, parseMission } from '../model/missionRecord';
import { hostInstance, parseRobotStatus } from '../model/robotStatus';
import type { HostInstance } from '../model/robotStatus';
import type { VlaStatus } from '../model/vla';
import type {
  ActivationOutcome,
  Attestation,
  CakeStatus,
  CakeStatusResult,
  CapabilityCheckOutcome,
  CapabilityCheckResult,
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
  VlaOp,
  ObservationRead,
  RecordOrigin,
  RobotConfirmOutcome,
  RobotStatusResult,
  ResolutionOutcome,
  SignatureCheckOutcome,
  SignatureCheckResult,
  StopAck,
  StopMissionPart,
  SubmitOutcome,
  TelemetryMessage,
  Unsubscribe,
  RunOutputResult,
} from './types';

/** The only contract version this console speaks. It is stated to the server, never inferred by it. */
const CONTRACT_VERSION = '1';

/**
 * The only Cake status version this console speaks. It is SEPARATE from the contract version on purpose: that surface
 * is versioned apart from the snapshot, so one may be raised without the other, and each is refused on its own terms.
 */
export const CAKE_STATUS_VERSION = '1';

/** Where this console looks for the API when the page it was served with did not say. */
const DEFAULT_API_PREFIX = '/api';

/**
 * The statuses the API answers when its guard, its router or its body reader refused the request. Every one of them is
 * decided BEFORE the runtime is reached (`console_api.security.Guard` runs first and nothing it refuses touches the
 * socket), so a command answered with one of these was not submitted. 5xx and a transport failure are NOT here: those
 * can happen after the runtime has the command, and the only honest thing then is to ask the runtime by id.
 */
const REFUSED_BEFORE_THE_RUNTIME: ReadonlySet<number> = new Set([400, 403, 404, 405, 409, 413, 415]);

/**
 * HOW LONG A STOP MAY GO UNANSWERED before this tab says its fate is not known, in milliseconds.
 *
 * A stop that hangs is worse than one that fails: the button sits on "Stopping" and the operator waits for a receipt
 * that never comes. It happened. Camera attempts that were dropped without ending their streams took every connection
 * the browser allows to this server, and Stop's POST queued behind them with no answer and no end. That leak is fixed
 * where it was (`design/useStreamDecode.ts`), and this bounds the wait whatever holds it up.
 *
 * The bound is longer than the API's own, so it does not cut short an answer that is on its way: the API gives the
 * runtime 10 s to answer a call (`RUNTIME_TIMEOUT_S`, apps/console-api/console_api/__main__.py) and answers
 * runtime_unavailable when it does not. A stop still unanswered after this long is 'unknown', which is what it is: the
 * request is abandoned, it is NOT sent again, and the receipt says whether it reached the runtime is not known.
 */
export const STOP_ANSWER_TIMEOUT_MS = 15_000;
/**
 * A VLA press is answered by the robot-side runner only after it read the runtime (twice when a read times out:
 * vla/runner.py RUNTIME_READ_TIMEOUT_S x RUNTIME_READ_ATTEMPTS), behind the console door's 12 s forward. Giving up
 * earlier read a press the runner was still deciding as "no answer".
 */
export const VLA_ANSWER_TIMEOUT_MS = 15_000;

/** How long a Stop refused `credential_superseded` waits for the page it reads for the current credential. */
const SERVED_PAGE_TIMEOUT_MS = 5_000;

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface EventSourceLike {
  onmessage: ((event: { data: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export interface HttpSourceOptions {
  base?: string;
  fetchImpl?: FetchLike;
  eventSourceFactory?: EventSourceFactory | null;
  /** Milliseconds before a reconnect attempt. Kept small and constant: this is a console on a desk, not a fleet. */
  reconnectDelayMs?: number;
  /** What the page was served with: the API prefix and the per-launch credential. Null when nothing served it. */
  boot?: ConsoleBoot | null;
  /** This tab's `client.session_id`. A test pins it; a tab mints its own. */
  sessionId?: string;
}

/** An answer that arrived, or a request whose fate this console does not know. */
type Answer = { kind: 'body'; status: number; ok: boolean; body: unknown } | { kind: 'unknown'; detail: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

/** One of the contract's three modes, or null. A token this console does not know is not a mode it may assume. */
function asDataMode(value: unknown): DataMode | null {
  return value === 'live' || value === 'replay' || value === 'synthetic' ? value : null;
}

/** The server's own reason for a refusal. A refusal without one is reported by its status, never as a guess. */
function refusalCode(body: unknown, status: number): string {
  const code = isRecord(body) ? asText(body['reason_code']) : null;
  return code ?? `refused_with_status_${String(status)}`;
}

/**
 * The mission's part of a stop answer, or null when the answer carried none.
 *
 * Only an explicit true is a mission whose stop was asked for. A part that does not say is not a mission that is
 * stopping, and it never turns a stop that stopped nothing into one that was taken.
 */
function parseStopMissionPart(value: unknown): StopMissionPart | null {
  if (!isRecord(value)) return null;
  return {
    mission_id: asText(value['mission_id']),
    stopping: value['stopping'] === true,
    reason_code: asText(value['reason_code']),
    state: asText(value['state']),
    carried: asBool(value['carried']),
  };
}

/**
 * A refusal's reason, read from the BODY first and from the status only when the body names none.
 *
 * A 404 is two different answers on the mission routes. The router's 404 (`not_found`) is a runtime or an API that
 * predates the route; the runtime's own `no_mission_recorded` also travels as a 404 and means the surface is there and
 * nothing has been started on it yet. Reading the status alone turned the second into the first, and the console then
 * said this runtime has no mission surface while the surface was there, waiting for its first Start.
 */
function missionRefusal(body: unknown, status: number, absentRoute: string): { reason_code: string; detail: string | null } {
  const code = isRecord(body) ? asText(body['reason_code']) : null;
  const detail = isRecord(body) ? asText(body['detail']) : null;
  if (status === 404 && (code === null || code === 'not_found')) return { reason_code: absentRoute, detail: null };
  return { reason_code: code ?? `refused_with_status_${String(status)}`, detail };
}

/**
 * One receipt. The four required fields, plus the contract's optional ones when the runtime recorded them.
 *
 * `payload_digest` is what makes a receipt say WHICH request it is a receipt for, and `first_seen_wall_utc` is when
 * that runtime first admitted the id. A runtime that records neither sends neither, and both read as null here, which
 * is "this journal cannot say" and never "the payloads agree".
 */
function parseCommandResult(value: unknown): CommandResult | null {
  if (!isRecord(value)) return null;
  const id = asText(value['command_id']);
  const status = asText(value['status']);
  if (id === null || status === null) return null;
  const allowed = ['accepted', 'spawning', 'running', 'finished', 'uncertain', 'rejected'];
  if (!allowed.includes(status)) return null;
  return {
    command_id: id,
    status: status as CommandResult['status'],
    reason_code: asText(value['reason_code']),
    run_id: asText(value['run_id']),
    payload_digest: asText(value['payload_digest']),
    first_seen_wall_utc: asText(value['first_seen_wall_utc']),
  };
}

/** The operator's statement as the runtime recorded it. A body without a finding carried no statement. */
function parseAttestation(value: unknown): Attestation | null {
  if (!isRecord(value)) return null;
  const finding = asText(value['finding']);
  if (finding === null) return null;
  return {
    finding,
    note: asText(value['note']),
    classification: asText(value['classification']),
    clears_block: value['clears_block'] === true,
    refused: asText(value['refused']),
    t_wall_utc: asText(value['t_wall_utc']),
  };
}

/**
 * One finished run. The runtime's journal sends eleven fields and this used to keep five; it keeps them all now, so a
 * reader can tell a synthetic pass from a physical one, and see what a run's evidence was, without opening the run.
 *
 * Every field is read as what it is: a number that is not a finite number, a flag that is not a boolean and a token
 * that is not a string all read as "the row does not say" rather than as a zero, a false or an invention. `data_mode`
 * and `bundle_digest` are optional on the contract and absent from a journal written before they existed; absent and
 * null are the same fact here, and neither is ever filled in from the mode this runtime happens to be in now.
 */
function parseHistoryEntry(value: unknown): HistoryEntry | null {
  if (!isRecord(value)) return null;
  const runId = asText(value['run_id']);
  if (runId === null) return null;
  return {
    run_id: runId,
    command_id: asText(value['command_id']),
    command_type: asText(value['command_type']),
    // The runtime's journal names this `started_wall_utc`; `started` is accepted for a source that spells it that way.
    started_wall_utc: asText(value['started_wall_utc']) ?? asText(value['started']),
    finished_wall_utc: asText(value['finished_wall_utc']) ?? asText(value['finished']),
    outcome: asText(value['outcome']),
    exit_status: asNumber(value['exit_status']),
    evidence_complete: asBool(value['evidence_complete']),
    evidence_gaps: asNumber(value['evidence_gaps']),
    artifact_digest: asText(value['artifact_digest']),
    adopted: asBool(value['adopted']),
    data_mode: asDataMode(value['data_mode']),
    bundle_digest: asText(value['bundle_digest']),
  };
}

/**
 * The per-record origins a recorded run is served with. The runtime writes one only for a record that HAS an origin
 * (the controller's own line), so this list is shorter than the record list and is keyed by the mission seq, never
 * read by position. An answer that carries none is a recording from a runtime that did not keep them, which is an
 * empty list here and not a failure: the records themselves are what a replay folds.
 */
function parseOrigins(body: unknown): RecordOrigin[] {
  const rows = isRecord(body) && Array.isArray(body['origins']) ? body['origins'] : null;
  if (rows === null) return [];
  const out: RecordOrigin[] = [];
  for (const row of rows) {
    if (!isRecord(row)) continue;
    const seq = asNumber(row['seq']);
    if (seq === null) continue;
    out.push({ seq, source_session: asText(row['source_session']), source_seq: asNumber(row['source_seq']) });
  }
  return out;
}

function parseEventsLine(value: unknown): EventsMessage {
  // The stream is a discriminated union of runtime lines. A drop notice is a NOTICE, not a record that failed to parse.
  if (isRecord(value) && 'dropped' in value) return { dropped: value['dropped'] };
  if (isRecord(value) && isRecord(value['resync'])) {
    const resync = value['resync'];
    const retained = asNumber(resync['retained_from_seq']);
    const reason = asText(resync['reason']);
    const missed = resync['missed'];
    if (retained !== null && Array.isArray(missed) && missed.length === 2) {
      const from = asNumber(missed[0]);
      const to = asNumber(missed[1]);
      if (from !== null && to !== null) return { resync: { retained_from_seq: retained, missed: [from, to], reason } };
    }
    // `cursor_ahead`: the runtime is answering a cursor from a log it is not serving any more, and says so by sending
    // no missing RANGE at all (demo_runtime.journal.EventLog.subscribe). Requiring a two-element array here made that
    // line malformed, so a tab reconnecting after the runtime was replaced counted a parse failure every time and
    // never resynchronized. A null range is now carried through as what it is.
    if (retained !== null && missed === null) return { resync: { retained_from_seq: retained, missed: null, reason } };
    return { malformed: value };
  }
  if (isContractEvent(value)) return { event: value };
  return { malformed: value };
}

export class HttpSource implements ConsoleSource {
  readonly kind = 'live' as const;
  readonly label = 'runtime over HTTP';

  private readonly base: string;
  private readonly prefix: string;
  private readonly boot: ConsoleBoot | null;
  private readonly fetchImpl: FetchLike;
  private readonly eventSourceFactory: EventSourceFactory | null;
  private readonly reconnectDelayMs: number;
  /**
   * Who this tab is, on every command it sends. The contract's command carries `client.session_id`, and the runtime
   * validates the command against that schema before it decides anything, so a command without one never reaches a
   * decision at all. It is minted here, locally and randomly: it names this tab and nothing about this machine.
   */
  private readonly sessionId: string;

  constructor(options: HttpSourceOptions = {}) {
    this.base = options.base ?? '';
    this.boot = options.boot ?? null;
    this.sessionId = options.sessionId ?? mintCommandId(16);
    // The page the API serves states where its API is. A page served another way falls back to the one prefix this
    // console knows; it does not scan for one.
    this.prefix = this.boot?.api_base ?? DEFAULT_API_PREFIX;
    const injected = options.fetchImpl;
    this.fetchImpl = injected ?? ((input, init) => globalThis.fetch(input, init));
    this.reconnectDelayMs = options.reconnectDelayMs ?? 1000;
    if (options.eventSourceFactory !== undefined) {
      this.eventSourceFactory = options.eventSourceFactory;
    } else {
      const Native = (globalThis as { EventSource?: new (url: string) => EventSourceLike }).EventSource;
      this.eventSourceFactory = Native === undefined ? null : (url: string): EventSourceLike => new Native(url);
    }
  }

  /** Whose credential this page was served, as the boot block said; null when it did not say (`api/boot.ts`). */
  get servedCredential(): ServedCredential | null {
    return this.boot?.served_credential ?? null;
  }

  /** True when this page was served with the credential the API's guard requires on every mutating request. */
  get canPresentCredential(): boolean {
    return this.boot?.credentials != null;
  }

  private url(path: string): string {
    return `${this.base}${this.prefix}${path}`;
  }

  private headers(): Record<string, string> {
    return commandHeaders(this.boot?.credentials ?? null);
  }

  /**
   * A request whose refusal must be READ, not thrown away.
   *
   * With `timeoutMs`, a request with no answer (or an answer whose body has not arrived) by then is abandoned and read
   * as 'unknown'. It is aborted so it stops holding a connection, and it is never resent. The timer settles the answer
   * itself rather than relying on the abort to do it, so a fetch that ignores the signal still ends the wait.
   */
  private async attempt(path: string, init?: RequestInit, timeoutMs?: number): Promise<Answer> {
    if (timeoutMs === undefined) return this.exchange(path, init);
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<Answer>((resolve) => {
      timer = setTimeout(() => {
        resolve({ kind: 'unknown', detail: `this tab had no answer to ${path} within ${String(timeoutMs / 1000)} s and stopped waiting` });
        controller.abort();
      }, timeoutMs);
    });
    try {
      return await Promise.race([this.exchange(path, { ...init, signal: controller.signal }), expired]);
    } finally {
      clearTimeout(timer);
    }
  }

  private async exchange(path: string, init?: RequestInit): Promise<Answer> {
    let response: Response;
    try {
      response = await this.fetchImpl(this.url(path), init);
    } catch (error) {
      return { kind: 'unknown', detail: error instanceof Error ? error.message : 'the request did not complete' };
    }
    try {
      return { kind: 'body', status: response.status, ok: response.ok, body: (await response.json()) as unknown };
    } catch {
      // A refusal whose body is unreadable is still a refusal with a status. A 2xx whose body is unreadable is not an
      // outcome at all.
      if (response.ok) return { kind: 'unknown', detail: `the answer to ${path} could not be read` };
      return { kind: 'body', status: response.status, ok: false, body: null };
    }
  }

  private async json(path: string, init?: RequestInit): Promise<unknown> {
    const response = await this.fetchImpl(this.url(path), init);
    if (!response.ok) throw new Error(`${path} answered ${String(response.status)}`);
    return (await response.json()) as unknown;
  }

  async hello(): Promise<HelloResult> {
    try {
      // The version this console speaks travels with the handshake, so the server refuses it outright rather than
      // letting it discover the mismatch one rejected command at a time.
      const body = await this.json(`/hello?contract_version=${CONTRACT_VERSION}`);
      if (!isRecord(body)) {
        return { ok: false, contract_version: null, runtime_session: null, reason_code: 'malformed_hello', read_only: true, commands_enabled: false, credential_owner: null };
      }
      const ok = body['ok'] === true;
      return {
        ok,
        contract_version: asText(body['contract_version']),
        runtime_session: asText(body['runtime_session']),
        reason_code: asText(body['reason_code']),
        read_only: body['read_only'] === true || !ok,
        // Absent means the server does not report it, and a server that does not say yes is not taken to have said yes.
        commands_enabled: ok && body['commands_enabled'] !== false && body['read_only'] !== true,
        credential_owner: asText(body['credential_owner']),
        bound_runtime_session: asText(body['bound_runtime_session']),
      };
    } catch {
      return { ok: false, contract_version: null, runtime_session: null, reason_code: 'runtime_unreachable', read_only: true, commands_enabled: false, credential_owner: null };
    }
  }

  async snapshot(): Promise<Snapshot> {
    // The runtime validates against the contract before it sends. The console does not revalidate: it would be a
    // second, weaker copy of the schema. A snapshot that does not parse as JSON throws and the screen says so.
    return (await this.json('/snapshot')) as Snapshot;
  }

  subscribeEvents(since: number, onMessage: (message: EventsMessage) => void, onReconnect: () => void): Unsubscribe {
    const factory = this.eventSourceFactory;
    if (factory === null) return () => undefined;
    let closed = false;
    let cursor = since;
    let stream: EventSourceLike | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const open = (): void => {
      if (closed) return;
      stream = factory(this.url(`/events?since=${String(cursor)}`));
      stream.onmessage = (message): void => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(message.data) as unknown;
        } catch {
          onMessage({ malformed: message.data });
          return;
        }
        const line = parseEventsLine(parsed);
        if ('event' in line) cursor = Math.max(cursor, line.event.seq);
        else if ('resync' in line) {
          // A resync WITH a range: the records up to the end of it are accounted for and the cursor moves past them.
          // A resync with NO range is `cursor_ahead`, and the cursor has to move BACK: it belongs to a log this
          // runtime is not serving, so keeping it would ask the next reconnect for a sequence that will never come
          // and get the same answer forever. It moves to the oldest seq the runtime says it can still serve.
          cursor =
            line.resync.missed === null
              ? Math.max(0, line.resync.retained_from_seq - 1)
              : Math.max(cursor, line.resync.missed[1]);
        }
        onMessage(line);
      };
      stream.onerror = (): void => {
        if (closed) return;
        stream?.close();
        stream = null;
        onReconnect();
        // Reopen from the cursor already reached, so the runtime can tell us about a gap instead of us inventing one.
        timer = setTimeout(open, this.reconnectDelayMs);
      };
    };

    open();
    return () => {
      closed = true;
      if (timer !== null) clearTimeout(timer);
      stream?.close();
    };
  }

  subscribeTelemetry(onMessage: (message: TelemetryMessage) => void): Unsubscribe {
    const factory = this.eventSourceFactory;
    if (factory === null) return () => undefined;
    const stream = factory(this.url('/telemetry'));
    stream.onmessage = (message): void => {
      try {
        const parsed = JSON.parse(message.data) as unknown;
        // One topic per camera, plus the older single-stream line. A line that names neither is not telemetry.
        const isTelemetry = isRecord(parsed)
          && (isRecord(parsed['tap']) || isRecord(parsed['preview'])
            || isRecord(parsed['preview:wrist']) || isRecord(parsed['preview:front']));
        if (isTelemetry) onMessage(parsed as TelemetryMessage);
      } catch {
        // Telemetry is latest-only and disposable: a line that does not parse is dropped, and the tap's own
        // drop counters are what the screen reports. Nothing here is evidence.
      }
    };
    return () => stream.close();
  }

  /**
   * One command, in the shape the CONTRACT defines it: contract_version, command_id, type, args and client, all on the
   * one object (packages/demo-contracts/schema/v1/command.schema.json, required, additionalProperties false).
   *
   * This body used to wrap the command, `{command_id, command: {type, args}, contract_version, client}`, on the reading
   * that the API takes the version off the envelope. It does not: `handle_post_command` calls `_unwrap` FIRST, which
   * replaces the body with the inner object and keeps nothing else, so the version check then ran against a command
   * that stated none. MEASURED against a running API: every command sent that way came back
   * `{"status":"rejected","reason_code":"contract_incompatible"}`, and the inner object had no `client.session_id`
   * either, which the runtime's own validation requires. The version is stated at both levels because the API checks
   * both.
   */
  private commandBody(commandId: string, command: Command): string {
    return JSON.stringify({
      contract_version: CONTRACT_VERSION,
      command_id: commandId,
      type: command.type,
      args: command.args,
      client: { session_id: this.sessionId, contract_version: CONTRACT_VERSION },
    });
  }

  async submit(commandId: string, command: Command): Promise<SubmitOutcome> {
    const answer = await this.attempt('/commands', {
      method: 'POST',
      headers: this.headers(),
      body: this.commandBody(commandId, command),
    });

    if (answer.kind === 'body') {
      const result = parseCommandResult(answer.body);
      if (result !== null) return { kind: 'result', result };
      if (!answer.ok && REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) {
        // The guard refused this before the runtime was touched. It was NOT submitted and it did NOT run; saying the
        // outcome is unknown would be false. The server's reason is passed through unaltered.
        return {
          kind: 'result',
          result: { command_id: commandId, status: 'rejected', reason_code: refusalCode(answer.body, answer.status), run_id: null },
        };
      }
    }

    // Anything else: the runtime may or may not hold this command. Ask it by id. The command is NOT sent again.
    const resolved = await this.resolve(commandId);
    if (resolved !== null) return { kind: 'result', result: resolved };
    return {
      kind: 'unresolved',
      command_id: commandId,
      detail: 'the response was lost and the runtime has not answered the follow-up; the command was not sent again',
    };
  }

  async resolve(commandId: string): Promise<CommandResult | null> {
    try {
      return parseCommandResult(await this.json(`/commands/${encodeURIComponent(commandId)}`));
    } catch {
      return null;
    }
  }

  /**
   * Put the uncertain command's question back to the runtime with the operator's declared finding.
   *
   * This request carries no command and mints no id: the id is the one the runtime already holds, in the PATH. The
   * runtime re-reads its own evidence, records the finding as an attestation beside it, and answers with what it
   * found, how it classified it and whether the block lifted. A refusal (the runtime found a live controller, the
   * command is not uncertain, the finding is not one it accepts) comes back with its own reason_code and is reported
   * as an ANSWER, because it is one. Only a request whose fate this console cannot read at all is 'unknown', and even
   * then nothing is retried and nothing is assumed to have changed.
   */
  async resolveUncertain(commandId: string, finding: string, note: string | null): Promise<ResolutionOutcome> {
    const answer = await this.attempt(`/commands/${encodeURIComponent(commandId)}/resolve`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ finding, note }),
    });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (body === null) {
      return { kind: 'unknown', detail: `the answer to the resolve could not be read (status ${String(answer.status)})` };
    }
    return {
      kind: 'answered',
      // Only an explicit true clears. A server that did not say did not say yes.
      cleared: body['cleared'] === true,
      cleared_by: asText(body['cleared_by']),
      classification: asText(body['classification']),
      reason_code: asText(body['reason_code']) ?? (answer.ok ? null : refusalCode(answer.body, answer.status)),
      evidence: isRecord(body['evidence']) ? body['evidence'] : null,
      attestation: parseAttestation(body['attestation']),
      contradiction: isRecord(body['contradiction']) ? body['contradiction'] : null,
    };
  }

  /**
   * Ask the runtime to stop, under an id this console minted and keeps.
   *
   * The id is the point. The body has always been a whole contract command; the API used to throw it away, so the
   * console's Stop was the one command that was not in the runtime's journal and the one that could be taken twice if
   * the answer was lost. The API submits it now, so the runtime records it under this id and answers a repeat of that
   * id with the recorded result. The id is returned here so a stop whose answer never arrived can be asked about by
   * id, exactly like every other command, rather than pressed again on a guess.
   *
   * A stop that found NO ACTIVE RUN AND NO MISSION is not a stop that was taken. The runtime says so (`ok` false with
   * no_active_run, or the older shape, `ok` true with no stop object beside it) and both read as refused here: the
   * operator pressed the button and nothing was stopped, which is a thing the screen must say rather than a green tick.
   * A stop that found no run but ended a MISSION was taken, and the mission part (`mission.stopping`) is what says so.
   *
   * A STOP REFUSED `credential_superseded` IS SENT ONCE MORE WITH THE CREDENTIAL OF THE PAGE THE SERVER SERVES NOW (redesign
   * r4 verify-r4 must_fix 4). The console server rotates its credential when any client makes it follow another runtime
   * session, and this tab learns of that only from its own reads, up to a poll later; in that window its Stop was refused
   * before it reached the runtime. "Reload to stop" asks a human to read the page again and press Stop there; this does
   * the same, once, for this press: it reads the page (GET /, this origin, what a reload fetches) for the credential in its
   * boot element and sends Stop ONE more time with it. The server is unchanged and still refuses the old value; the new
   * one is used for that one request and kept nowhere, so nothing else this tab sends carries it. Only a page served with
   * a boot element takes over, only on `credential_superseded`, and never a third time.
   */
  async stop(): Promise<StopAck> {
    const held = this.boot?.credentials ?? null;
    const first = await this.sendStop(held);
    if (first.outcome !== 'refused' || first.reason !== 'credential_superseded' || held === null) return first;
    const served = await this.servedCredentials();
    const notSent = (reason: string): StopAck => ({
      ...first,
      takeover: { refused_command_id: first.command_id, sent: false, not_sent_reason: reason, runtime_session: null },
    });
    if (!served.ok) return notSent(served.reason);
    if (served.credentials.credential === held.credential) return notSent('the page carries the credential this tab already holds');
    const second = await this.sendStop(served.credentials);
    return { ...second, takeover: { refused_command_id: first.command_id, sent: true, not_sent_reason: null, runtime_session: served.runtime_session } };
  }

  /**
   * THE CREDENTIAL IN THE PAGE THE CONSOLE SERVER SERVES NOW: GET / on this origin, the document a reload fetches, and its
   * boot element (`bootFromHtml`). A READ; bounded so a Stop never waits on it for long. Its value is returned to the one
   * caller and not kept.
   */
  private async servedCredentials(): Promise<{ ok: true; credentials: ConsoleCredential; runtime_session: string | null } | { ok: false; reason: string }> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SERVED_PAGE_TIMEOUT_MS);
    try {
      const response = await this.fetchImpl(`${this.base}/`, { method: 'GET', cache: 'no-store', headers: { accept: 'text/html' }, signal: controller.signal });
      if (!response.ok) {
        let body: unknown = null;
        try {
          body = (await response.json()) as unknown;
        } catch {
          body = null;
        }
        return { ok: false, reason: refusalCode(body, response.status) };
      }
      const boot = bootFromHtml(await response.text());
      if (boot === null || boot.credentials === null) return { ok: false, reason: 'the page carries no credential' };
      return { ok: true, credentials: boot.credentials, runtime_session: boot.served_credential?.runtime_session ?? null };
    } catch (error) {
      return { ok: false, reason: error instanceof Error ? error.message : 'the page could not be read' };
    } finally {
      clearTimeout(timer);
    }
  }

  /** One Stop, under a fresh id, with the credential given (the page's own, or the one a takeover read). */
  private async sendStop(credentials: ConsoleCredential | null): Promise<StopAck> {
    const commandId = mintCommandId();
    const answer = await this.attempt(
      '/stop',
      { method: 'POST', headers: commandHeaders(credentials), body: this.commandBody(commandId, { type: 'stop', args: {} }) },
      STOP_ANSWER_TIMEOUT_MS,
    );
    if (answer.kind === 'unknown') {
      // The request went out and its fate is not known. The id is carried back so the console can ask by it.
      return { ok: false, command_id: commandId, result: null, requested_mono_ns: null, run_id: null, reason: answer.detail, outcome: 'unknown', mission: null };
    }
    const body = isRecord(answer.body) ? answer.body : null;
    const result = body === null ? null : parseCommandResult(body['command']);
    const stop = body !== null && isRecord(body['stop']) ? body['stop'] : null;
    // THE MISSION'S PART, read whatever the run's part says. A mission held on a capability holds the run slot with no
    // controller run under it, so a Stop that ended it can come back with no run stopped; that press was TAKEN, and it
    // is read as taken here on the strength of the mission part rather than on the run's alone.
    const mission = body === null ? null : parseStopMissionPart(body['mission']);
    const missionTaken = mission !== null && mission.stopping;
    if (!answer.ok || body === null || body['ok'] !== true || (stop === null && !missionTaken)) {
      const code = body === null ? null : asText(body['reason_code']);
      return {
        ok: false,
        command_id: commandId,
        result,
        requested_mono_ns: null,
        run_id: result?.run_id ?? null,
        reason: answer.ok
          ? (code ?? 'the runtime took no run and no mission to stop, so nothing was stopped')
          : refusalCode(answer.body, answer.status),
        outcome: 'refused',
        mission,
      };
    }
    return {
      ok: true,
      command_id: commandId,
      result,
      // The journaled path has no field for the runtime's monotonic stamp and this console does not invent one from
      // its own clock. The run's stop.requested record carries the real number.
      requested_mono_ns: stop === null ? null : asNumber(stop['requested_mono_ns']),
      // The run the RUNTIME says it stopped. A stop that ended only a mission names none, and none is invented here.
      run_id: stop === null ? null : asText(stop['run_id']),
      reason: null,
      outcome: 'taken',
      mission,
    };
  }

  async observationRecord(observationId: string): Promise<ObservationRead<ObservationRecordDto>> {
    return this.observation(observationId, 'record', parseObservationRecord);
  }

  async observationPerception(observationId: string): Promise<ObservationRead<PerceptionDto>> {
    return this.observation(observationId, 'perception', parsePerception);
  }

  /**
   * THE RUNTIME'S OBSERVATION ANSWER IS AN ENVELOPE (redesign r4 round 7 item 1): `{"ok": true, "observation_id": ...,
   * "what": ..., "data": <projected record>}` (daemon `_op_observation`), forwarded as it is by the console API
   * (`handle_observation`). Only `data` is the record; parsing the envelope itself read every field as null. A refusal
   * keeps its reason_code, which is passed on; an envelope for another observation, or one with no data, is not read.
   */
  private async observation<T>(observationId: string, what: 'record' | 'perception', parse: (value: unknown) => T): Promise<ObservationRead<T>> {
    const answer = await this.attempt(`/observations/${encodeURIComponent(observationId)}/${what}`);
    if (answer.kind === 'unknown') return { data: null, reason: 'no answer came' };
    const body = answer.body;
    if (!answer.ok || !isRecord(body) || body['ok'] !== true) {
      return { data: null, reason: `the console server answered ${refusalCode(body, answer.status)}` };
    }
    const servedFor = asText(body['observation_id']);
    if (servedFor !== null && servedFor !== observationId) return { data: null, reason: `the answer was for another observation, ${servedFor}` };
    const data = body['data'];
    if (!isRecord(data)) return { data: null, reason: 'the answer carried no data' };
    return { data: parse(data), reason: null };
  }

  frameUrl(observationId: string, what: 'raw' | 'overlay', maxWidth: number): string {
    return this.url(`/observations/${encodeURIComponent(observationId)}/frame?what=${what}&w=${String(Math.round(maxWidth))}`);
  }

  previewUrl(role: string): string {
    return this.url(`/preview.mjpg?role=${encodeURIComponent(role)}`);
  }

  /**
   * The finished runs. The API forwards the runtime's answer as it is: an envelope, `{"ok": true, "runs": [...]}`, or a
   * refusal with a reason. A list that could not be read is reported as NOT READ; it is never shown as "no run has
   * finished", which is a different fact.
   */
  async history(): Promise<HistoryResult> {
    const answer = await this.attempt('/history');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) return { ok: false, reason_code: refusalCode(answer.body, answer.status) };
    const rows = Array.isArray(answer.body) ? answer.body : isRecord(answer.body) && Array.isArray(answer.body['runs']) ? answer.body['runs'] : null;
    if (rows === null) return { ok: false, reason_code: 'the answer carried no list of runs' };
    const out: HistoryEntry[] = [];
    for (const item of rows) {
      const entry = parseHistoryEntry(item);
      if (entry !== null) out.push(entry);
    }
    return { ok: true, entries: out };
  }

  /**
   * The recorded events of one run, for replay. An empty or unreadable answer is a FAILURE, not a run in which nothing
   * happened: folding zero records would draw an empty screen under a badge that says it is a recording.
   */
  async runOutput(runId: string, stream: 'stdout' | 'stderr'): Promise<RunOutputResult> {
    const answer = await this.attempt(`/history/${encodeURIComponent(runId)}/output?stream=${stream}`);
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) return { ok: false, reason_code: refusalCode(answer.body, answer.status) };
    const body = answer.body;
    if (!isRecord(body) || typeof body['text'] !== 'string') {
      return { ok: false, reason_code: 'the answer carried no output for this run' };
    }
    return {
      ok: true,
      run_id: runId,
      stream,
      text: body['text'],
      // The runtime states the size of the whole file beside the part it read. A missing number is reported as the
      // length of what arrived rather than as a zero, because a zero would read as an empty log.
      total_bytes: typeof body['total_bytes'] === 'number' ? body['total_bytes'] : body['text'].length,
      truncated: body['truncated'] === true,
    };
  }

  /**
   * The Cake resident status. A READ: no body, no command id, nothing that can reach the command path.
   *
   * A runtime that does not serve this route is one that predates the surface, and it is reported as such rather than
   * as a resident that answered with nothing. The version is checked before anything is read out of the answer: a
   * surface this console does not speak is refused, not guessed at, which is the same rule the contract handshake uses.
   */
  async cakeStatus(): Promise<CakeStatusResult> {
    const answer = await this.attempt('/cake');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) {
      return { ok: false, reason_code: answer.status === 404 ? 'this runtime does not report a Cake resident' : refusalCode(answer.body, answer.status) };
    }
    const body = answer.body;
    if (!isRecord(body) || body['cake_status_version'] !== CAKE_STATUS_VERSION) {
      return { ok: false, reason_code: 'the answer is not a Cake status this console speaks' };
    }
    return { ok: true, status: body as unknown as CakeStatus };
  }

  /**
   * The mission record. A READ, on the same terms as every other read here.
   *
   * The route answers two different ways and both are honoured: a mission record, or `{"ok": false, "reason_code": ...}`
   * when this runtime is carrying no mission. The second is not a failure of the read and it is not an empty mission;
   * it is the runtime saying there is nothing to describe, and the screen says exactly that.
   */
  async mission(): Promise<MissionResult> {
    const answer = await this.attempt('/mission');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) {
      const refused = missionRefusal(answer.body, answer.status, 'this runtime does not report a mission');
      return { ok: false, reason_code: refused.reason_code, detail: refused.detail };
    }
    if (isRecord(answer.body) && answer.body['ok'] === false) {
      return {
        ok: false,
        reason_code: asText(answer.body['reason_code']) ?? 'the runtime is carrying no mission',
        detail: asText(answer.body['detail']),
      };
    }
    const mission = parseMission(answer.body);
    if (mission === null) return { ok: false, reason_code: 'the answer is not a mission record this console reads' };
    return { ok: true, mission };
  }

  async missionCandidates(): Promise<MissionCandidatesResult> {
    const answer = await this.attempt('/mission/candidates');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) {
      return {
        ok: false,
        reason_code: answer.status === 404 ? 'this runtime offers no prepared candidates' : refusalCode(answer.body, answer.status),
      };
    }
    if (isRecord(answer.body) && answer.body['ok'] === false) {
      return { ok: false, reason_code: asText(answer.body['reason_code']) ?? 'the runtime served no candidate list' };
    }
    return { ok: true, candidates: parseCandidates(answer.body) };
  }

  /**
   * Activate ONE prepared candidate, by key.
   *
   * The body carries the key and nothing else. That is deliberate and is pinned by a test: bytes, a path or an
   * operation name in this body would be this console choosing what the resident admits, which is not its decision to
   * make and is not a thing a browser should be able to send at all.
   *
   * A refusal by the API's guard is a refusal: it was decided before the runtime was touched, so the request did not
   * land. Anything else that cannot be read is UNKNOWN, and nothing is sent again on it: the runtime's own mission
   * record is what says whether an activation happened, and it is read back rather than guessed at here.
   */
  async vlaStatus(): Promise<VlaStatus> {
    const answer = await this.attempt('/vla/status', undefined, 4000);
    // A console started without --vla-endpoint has no VLA door: that is not a runner that is down.
    if (answer.kind === 'body' && answer.status === 404 && isRecord(answer.body) && answer.body['reason_code'] === 'vla_not_configured') return { state: 'absent' };
    if (answer.kind === 'unknown' || !answer.ok || !isRecord(answer.body)) return { state: 'unreachable' };
    return answer.body as unknown as VlaStatus;
  }

  async vlaCommand(op: VlaOp): Promise<{ ok: boolean; answer: unknown }> {
    const answer = await this.attempt(`/vla/${op}`, { method: 'POST', headers: this.headers(), body: '{}' }, VLA_ANSWER_TIMEOUT_MS);
    if (answer.kind === 'unknown') return { ok: false, answer: answer.detail };
    return { ok: answer.ok, answer: answer.body };
  }

  async activateCandidate(candidateKey: string): Promise<ActivationOutcome> {
    const answer = await this.attempt('/mission/activate', {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ candidate_key: candidateKey }),
    });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) return { kind: 'refused', reason_code: refusalCode(answer.body, answer.status) };
      return { kind: 'unknown', detail: `the activation answered ${String(answer.status)} and this console cannot say whether it was taken` };
    }
    if (body === null || body['ok'] === false) {
      return { kind: 'refused', reason_code: body === null ? 'the answer to the activation could not be read' : asText(body['reason_code']) ?? 'the runtime refused the activation without saying why' };
    }
    const state = asText(body['activation_state']);
    if (state === null) {
      return { kind: 'unknown', detail: 'the answer carried no activation state, so what became of the request is not known from it' };
    }
    return {
      kind: 'answered',
      operation_uuid: asText(body['operation_uuid']),
      activation_state: state,
      outcome: asText(body['outcome']),
      transaction_phase: asText(body['transaction_phase']),
      detail: asText(body['detail']),
    };
  }

  /**
   * Start the mission. The body is EMPTY OF CHOICES: an empty object, so the runtime starts its own mission and nothing
   * this console sends can name a target, a file or a capability. It carries the headers every mutating request carries.
   *
   * A status the guard or the runtime answered with is a REFUSAL, with the runtime's own reason and detail: nothing was
   * started. A 2xx that says `ok` is a mission the runtime wrote down, by id. Anything else is UNKNOWN, and it is never
   * sent again: the mission record is read back to find out whether one started.
   */
  async missionStart(): Promise<MissionStartOutcome> {
    const answer = await this.attempt('/mission/start', { method: 'POST', headers: this.headers(), body: JSON.stringify({}) });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) return { kind: 'refused', ...missionRefusal(answer.body, answer.status, 'this runtime does not offer a mission start') };
      return { kind: 'unknown', detail: `the mission start answered ${String(answer.status)} and this console cannot say whether a mission started` };
    }
    if (body === null) return { kind: 'unknown', detail: 'the answer to the mission start could not be read' };
    if (body['ok'] !== true) {
      return { kind: 'refused', reason_code: asText(body['reason_code']) ?? 'the runtime did not start a mission and did not say why', detail: asText(body['detail']) };
    }
    return { kind: 'started', mission_id: asText(body['mission_id']) };
  }

  /**
   * TRY AGAIN: POST /api/mission/retry. Offered only while the mission is held for the operator's decision after a
   * failure; the runtime parks the arm with the proven exit and starts a fresh attempt, each its own run. The body names
   * the mission whose hold the operator saw (`mission_id`), so a press meant for one mission can never retry another. A status the guard or the runtime answered with is a refusal with its own reason (a runtime without the
   * route says so); a 2xx that says `ok` is a receipt; anything else is UNKNOWN and is never sent again: the mission
   * record, read back, says whether an attempt started.
   */
  async missionRetry(missionId: string): Promise<MissionStartOutcome> {
    const answer = await this.attempt('/mission/retry', { method: 'POST', headers: this.headers(), body: JSON.stringify({ mission_id: missionId }) });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) return { kind: 'refused', ...missionRefusal(answer.body, answer.status, 'this runtime does not offer a mission retry') };
      return { kind: 'unknown', detail: `the mission retry answered ${String(answer.status)} and this console cannot say whether an attempt started` };
    }
    if (body === null) return { kind: 'unknown', detail: 'the answer to the mission retry could not be read' };
    if (body['ok'] !== true) {
      return { kind: 'refused', reason_code: asText(body['reason_code']) ?? 'the runtime did not start an attempt and did not say why', detail: asText(body['detail']) };
    }
    return { kind: 'started', mission_id: asText(body['mission_id']) };
  }

  /**
   * Stop the mission through its own door. The runtime answers with the mission's part and puts the run's stop beside
   * it under `run_stop`; a runtime with no mission surface still stops the run and says both things.
   */
  async missionStop(): Promise<MissionStopOutcome> {
    const answer = await this.attempt('/mission/stop', { method: 'POST', headers: this.headers(), body: JSON.stringify({}) }, STOP_ANSWER_TIMEOUT_MS);
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) return { kind: 'refused', ...missionRefusal(answer.body, answer.status, 'this runtime does not offer a mission stop') };
      return { kind: 'unknown', detail: `the mission stop answered ${String(answer.status)} and this console cannot say whether it was taken` };
    }
    const mission = parseStopMissionPart(body);
    if (body === null || mission === null) return { kind: 'unknown', detail: 'the answer to the mission stop could not be read' };
    // Whether the RUN's part stopped something. The supervisor answers the run it signalled ({requested_mono_ns, run_id})
    // or null when no run was active. An answer with no `run_stop` key at all said nothing about a run, which is null
    // here and is not a run that was not stopped.
    const runStopped = 'run_stop' in body ? isRecord(body['run_stop']) : null;
    return { kind: 'answered', mission, run_stopped: runStopped };
  }

  /**
   * The robot's resident. A READ: no body, no command id, nothing that can reach the command path. The version is
   * checked before anything is read out of it; a surface this console does not speak is refused, not guessed at.
   */
  async robotStatus(): Promise<RobotStatusResult> {
    const answer = await this.attempt('/robot');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) {
      return { ok: false, reason_code: answer.status === 404 ? 'this runtime does not report a robot' : refusalCode(answer.body, answer.status) };
    }
    if (isRecord(answer.body) && answer.body['ok'] === false) {
      return { ok: false, reason_code: asText(answer.body['reason_code']) ?? 'the runtime served no robot status' };
    }
    const status = parseRobotStatus(answer.body);
    if (status === null) return { ok: false, reason_code: 'the answer is not a robot status this console speaks' };
    return { ok: true, status };
  }

  /**
   * Confirm the connected robot: its resident session and its supervised host instance, exactly as displayed.
   *
   * The same headers every mutating request carries, the same refusal handling the activation has: a status the guard
   * answers before the runtime is a refusal, anything that cannot be read is UNKNOWN and nothing is sent again on it.
   * The body is EXACTLY {session_uuid, host_instance: {slot_instance, spawn_sequence, pid}}: it is rebuilt field by
   * field here, so nothing else the caller's object might carry can travel with it.
   */
  async confirmRobotSession(sessionUuid: string, instance: HostInstance): Promise<RobotConfirmOutcome> {
    const request = {
      session_uuid: sessionUuid,
      host_instance: { slot_instance: instance.slot_instance, spawn_sequence: instance.spawn_sequence, pid: instance.pid },
    };
    const answer = await this.attempt('/robot/confirm-session', {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify(request),
    });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) return { kind: 'refused', reason_code: refusalCode(answer.body, answer.status) };
      return { kind: 'unknown', detail: `the confirmation answered ${String(answer.status)} and this console cannot say whether it was taken` };
    }
    if (body === null) return { kind: 'unknown', detail: 'the answer to the confirmation could not be read' };
    return {
      kind: 'answered',
      ok: body['ok'] === true,
      reason_code: asText(body['reason_code']),
      confirmed_session_uuid: asText(body['confirmed_session_uuid']),
      confirmed_host_instance: hostInstance(body['confirmed_host_instance']),
      confirmed_wall_utc: asText(body['confirmed_wall_utc']),
      motion_permitted: asBool(body['motion_permitted']),
    };
  }

  /**
   * The capability slot check's read. A refusal keeps the runtime's reason AND its detail, because "no check here" has
   * several causes (not LIVE, no prepared Plan, no capability binding) and an operator is owed which one.
   */
  async capabilityCheck(): Promise<CapabilityCheckResult> {
    const answer = await this.attempt('/capability/check');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer', detail: null };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok || body === null || body['ok'] === false) {
      const code = answer.status === 404 && (body === null || asText(body['reason_code']) === 'not_found')
        ? 'this runtime does not offer a capability slot check'
        : refusalCode(answer.body, answer.status);
      return { ok: false, reason_code: code, detail: body === null ? null : asText(body['detail']) };
    }
    const state = parseCapabilityCheckState(body);
    if (state === null) return { ok: false, reason_code: 'the answer is not a capability slot check this console speaks', detail: null };
    return { ok: true, state };
  }

  /**
   * Apply ONE prepared Plan to the robot's capability slot and read it back. NO MOTION.
   *
   * The body is EXACTLY {candidate_key, payload_digest, session_uuid}, rebuilt field by field here, so nothing else the
   * caller's values might carry can travel with them: no bytes, no path, no operation. Every refusal the runtime answers
   * precedes its submit, so a status the guard or the runtime refused with is a refusal. Anything else that cannot be read
   * is UNKNOWN and nothing is sent again on it: the check's own record is read back to find out what became of it.
   */
  async applyCapabilityCheck(candidateKey: string, payloadDigest: string, sessionUuid: string): Promise<CapabilityCheckOutcome> {
    const request = { candidate_key: candidateKey, payload_digest: payloadDigest, session_uuid: sessionUuid };
    const answer = await this.attempt('/capability/check', { method: 'POST', headers: this.headers(), body: JSON.stringify(request) });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) {
        return { kind: 'refused', reason_code: refusalCode(answer.body, answer.status), detail: body === null ? null : asText(body['detail']) };
      }
      return { kind: 'unknown', detail: `the check answered ${String(answer.status)} and this console cannot say whether it was submitted` };
    }
    if (body === null) return { kind: 'unknown', detail: 'the answer to the check could not be read' };
    if (body['ok'] === false) return { kind: 'refused', reason_code: asText(body['reason_code']) ?? 'the runtime refused the check without saying why', detail: asText(body['detail']) };
    const state = asText(body['activation_state']);
    if (state === null) return { kind: 'unknown', detail: 'the answer carried no state, so what became of the request is not known from it' };
    const after = isRecord(body['host_after']) ? body['host_after'] : null;
    return {
      kind: 'answered',
      operation_uuid: asText(body['operation_uuid']),
      activation_state: state,
      transaction_phase: asText(body['transaction_phase']),
      detail: asText(body['detail']),
      resident_session_changed: asBool(body['resident_session_changed']),
      host_after_running: after === null ? null : asBool(after['host_running']),
    };
  }

  /**
   * The signature policy check's read. A refusal keeps the runtime's reason AND its detail, because "no check here" has
   * several causes (not LIVE, not on the robot, no release named) and an operator is owed which one.
   */
  async signatureCheck(): Promise<SignatureCheckResult> {
    const answer = await this.attempt('/capability/signature');
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer', detail: null };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok || body === null || body['ok'] === false) {
      const code = answer.status === 404 && (body === null || asText(body['reason_code']) === 'not_found')
        ? 'this runtime does not offer a signature policy check'
        : refusalCode(answer.body, answer.status);
      return { ok: false, reason_code: code, detail: body === null ? null : asText(body['detail']) };
    }
    const state = parseSignatureCheckState(body);
    if (state === null) return { ok: false, reason_code: 'the answer is not a signature policy check this console speaks', detail: null };
    return { ok: true, state };
  }

  /**
   * Run the release verifier on the robot over every deployed capsule. NO MOTION, no slot.
   *
   * The body is EXACTLY an empty object, so nothing a caller holds can travel with it: no capsule, no key, no path, no
   * policy. Every refusal the runtime answers precedes the first verifier child, so a status the guard or the runtime
   * refused with is a refusal. Anything else that cannot be read is UNKNOWN and nothing is sent again on it: the check's
   * own record is read back to find out what became of it.
   */
  async runSignatureCheck(): Promise<SignatureCheckOutcome> {
    const answer = await this.attempt('/capability/signature', { method: 'POST', headers: this.headers(), body: JSON.stringify({}) });
    if (answer.kind === 'unknown') return { kind: 'unknown', detail: answer.detail };
    const body = isRecord(answer.body) ? answer.body : null;
    if (!answer.ok) {
      if (REFUSED_BEFORE_THE_RUNTIME.has(answer.status)) {
        return { kind: 'refused', reason_code: refusalCode(answer.body, answer.status), detail: body === null ? null : asText(body['detail']) };
      }
      return { kind: 'unknown', detail: `the signature check answered ${String(answer.status)} and this console cannot say whether the verifier ran` };
    }
    if (body === null) return { kind: 'unknown', detail: 'the answer to the signature check could not be read' };
    if (body['ok'] === false) return { kind: 'refused', reason_code: asText(body['reason_code']) ?? 'the runtime refused the check without saying why', detail: asText(body['detail']) };
    if (!Array.isArray(body['rows'])) return { kind: 'unknown', detail: 'the answer carried no verdict table, so what became of the request is not known from it' };
    const count = (name: string): number => (typeof body[name] === 'number' && Number.isFinite(body[name]) ? (body[name] as number) : 0);
    return {
      kind: 'answered',
      check_uuid: asText(body['check_uuid']),
      as_expected: asBool(body['as_expected']),
      complete: asBool(body['complete']),
      rows_as_expected: count('rows_as_expected'),
      rows_not_as_expected: count('rows_not_as_expected'),
      rows_skipped: count('rows_skipped'),
      rows_error: count('rows_error'),
    };
  }

  async historyEvents(runId: string): Promise<HistoryEventsResult> {
    const answer = await this.attempt(`/history/${encodeURIComponent(runId)}/events`);
    if (answer.kind === 'unknown') return { ok: false, reason_code: 'the runtime did not answer' };
    if (!answer.ok) return { ok: false, reason_code: refusalCode(answer.body, answer.status) };
    const rows = Array.isArray(answer.body) ? answer.body : isRecord(answer.body) && Array.isArray(answer.body['events']) ? answer.body['events'] : null;
    if (rows === null) return { ok: false, reason_code: 'the answer carried no records for this run' };
    const events = rows.filter(isContractEvent);
    if (events.length !== rows.length) {
      // Some line the reducer could not fold at all. It is reported, not quietly dropped from a replay.
      return { ok: false, reason_code: `${String(rows.length - events.length)} of ${String(rows.length)} records could not be read` };
    }
    return { ok: true, run_id: runId, events, origins: parseOrigins(answer.body) };
  }
}
