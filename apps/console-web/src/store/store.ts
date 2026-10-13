// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The store: one external source of truth for the whole screen, read through useSyncExternalStore.
//
// Three things are deliberately kept apart here:
//  - the DURABLE view state, folded from contract events by the pure reducer;
//  - TELEMETRY, which is latest-only and disposable and is never folded into the durable state;
//  - the DECISION FRAME's fetched record and perception, which follow the pinned frame and nothing else.

import type { Command, CommandResult, Snapshot } from '@kiwi/demo-contracts';
import type { ContractEvent } from '../contracts/envelope';
import type {
  ActivationOutcome,
  CakeStatus,
  CapabilityCheckOutcome,
  SignatureCheckOutcome,
  ConsoleSource,
  HelloResult,
  HistoryResult,
  MissionStartOutcome,
  PreviewSample,
  RecordOrigin,
  ResolutionOutcome,
  RobotConfirmOutcome,
  StopAck,
  SubmitOutcome,
  TapSample,
  UnresolvedCommand,
  Unsubscribe,
} from '../api/types';
import type { CapabilityCheckState } from '../model/capabilityCheck';
import type { SignatureCheckState } from '../model/signatureCheck';
import type { MissionCandidate, MissionRecord } from '../model/missionRecord';
import type { HostInstance, RobotStatus } from '../model/robotStatus';
import type { ServedCredential } from '../api/boot';
import { mintCommandId } from '../api/commandId';
import { previewRoleOf, previewSampleOf } from '../api/types';
import { frameIdentityOf, isNewFrame, withFrameAt } from '../model/frameProgression';
import type { FrameIdentity } from '../model/frameProgression';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';
import {
  applyEvent,
  applyResync,
  applySnapshot,
  foldEvents,
  initialState,
  markCommandsDisabled,
  markContractIncompatible,
  markDropNotice,
  markMalformedLine,
  markReconnect,
  mergeRunRecords,
} from '../model/reducer';
import type { ViewState } from '../model/types';
import { activeRunOnRecord, selectedRun, viewForSelectedRun } from '../model/selectedRun';
import { missionEnded } from '../model/verdict';
import type { ResultDetail } from '../model/acceptance';
import { resultForCurrentRun, TELEMETRY_SILENT_AFTER_MS } from '../model/steps';
import { arrivalSince, SNAPSHOT_CURRENT_WITHIN_MS } from '../model/arrivalSince';
import { checkOffWords } from '../model/capabilityCheck';
import { signatureOffWords } from '../model/signatureCheck';
import {
  activationPressReceipt,
  checkPressReceipt,
  commandPressReceipt,
  confirmPressReceipt,
  missionRetryReceipt,
  missionStartReceipt,
  signaturePressReceipt,
  stopReceipt,
} from '../design/receipts';
import type { Receipt } from '../design/receipts';
import { answerPress, appendPress } from './presses';
import { composeTimeline, keptEvent, mergeTimeline } from '../model/missionTimeline';
import type { LinkChange, TimelineEntry } from '../model/missionTimeline';
import { ACKNOWLEDGED_KEY } from '../model/outcomeCard';
import { runtimeSessionStart } from '../model/currency';
import { newestScanRun, watchedScans } from '../model/sceneRecords';
import type { AckRecord } from '../model/outcomeCard';
import type { PressControl, PressEntry } from './presses';

/** How often this page reads the snapshot, the robot, the mission and the checks. Stated on the Connections tab. */
export const SNAPSHOT_POLL_MS = 5000;

/** How long after a run's end the mission is read once more while its record still says RUNNING with no run in progress. */
const MISSION_REREAD_AFTER_RUN_END_MS = 1000;

/** How long after a run's end the snapshot is read once more while it still names that run as active. */
const SNAPSHOT_REREAD_AFTER_RUN_END_MS = 1000;

/**
 * How many times, a second apart, the snapshot is read again after a run's or a mission's end while it still says the
 * run or the mission holds the slot: until the next poll would have read it anyway (should_fix honesty S1).
 */
const SNAPSHOT_REREADS_AFTER_END = Math.max(1, Math.floor(SNAPSHOT_POLL_MS / SNAPSHOT_REREAD_AFTER_RUN_END_MS) - 1);

/**
 * How often, and how many times, the mission is read again after an activation the runtime OBSERVED while the record read
 * after the answer still says the mission waits (redesign r4 verify-r4 must_fix 2). The runtime writes the resume only at
 * the carrying thread's next slot read (`demo_runtime/mission_coordinator.py`, `CAPABILITY_POLL_S` = 0.5 s), after the
 * answer; 250 ms apart for 2 s covers that with room for a slow slot read, and then the poll carries on.
 */
const MISSION_REREAD_AFTER_ACTIVATION_MS = 250;
const MISSION_REREADS_AFTER_ACTIVATION = 8;

/**
 * Whether the mission record says it still waits for Cake while its newest activation is one the runtime OBSERVED: the
 * state in which the runtime's own resume is still to be written. A stop asked for ends the wait another way.
 */
function observedWhileWaiting(mission: MissionRecord | null): boolean {
  if (mission === null || mission.state !== 'WAITING_FOR_CAPABILITY' || mission.stop_requested === true) return false;
  const newest = mission.activations[mission.activations.length - 1];
  return newest !== undefined && newest.activation_state === 'OBSERVED';
}

/** The camera a single-camera reader means. Every role is kept in `previews`; this one is also kept beside it. */
const DEFAULT_PREVIEW_ROLE = 'wrist';

/**
 * How often REPLACEABLE telemetry is republished to the screen, at most.
 *
 * The tap arrives about ten times a second and the preview beside it, and every line was waking React on its own. The
 * samples are latest-only and a superseded one is never shown, so nothing is lost by holding one until the window
 * closes: what arrives inside the window is merged and published once, which puts the screen at roughly eight
 * publications a second.
 *
 * This is for telemetry and for nothing else. Ordered mission events, stop acknowledgements and command results go
 * straight through, because those are things that happened once and the screen must not learn about them a beat late.
 */
const TELEMETRY_FLUSH_MS = 120;

/** The snapshot fields the timeline is composed from; a patch touching one of them composes it again. */
const TIMELINE_INPUTS: readonly (keyof StoreSnapshot)[] = ['mission', 'history', 'missionRunEvents', 'presses', 'links', 'missionCandidates', 'sourceError'];

export interface CommandTrack {
  command_id: string;
  type: Command['type'];
  /**
   * When THIS TAB learned of the command, on this computer's clock. For a command this tab submitted that is when it
   * was sent. For an ADOPTED one it is when the snapshot handed it over after a reload, and the honest answer to when
   * the runtime admitted it is `first_seen_wall_utc`, which the runtime's own journal holds.
   */
  submitted_at: number;
  result: CommandResult | null;
  /** Set when the response was lost and the follow-up did not answer. The command is never resent. */
  unresolved: string | null;
  /**
   * True for a command this tab did not submit and recovered from the runtime's snapshot after a reload. It is kept
   * apart because "I sent this" and "the runtime says this is running" are different claims, and a reloaded tab may
   * only make the second one.
   */
  adopted?: boolean;
  /** The runtime seq the run of this command started at, when the snapshot named one. The storage-free cursor. */
  started_seq?: number | null;
  /** When the RUNTIME first admitted this id, out of its journal. Null when it did not record one. */
  first_seen_wall_utc?: string | null;
}

/**
 * Whose runtime this tab is talking to, and whether what it holds is still good for commanding it.
 *
 * None of this is inferred. `booted_runtime_session` is the session the handshake named when this tab started and is
 * what readiness was established against; `current_runtime_session` is what the latest snapshot says. When they differ
 * the runtime was replaced under an open tab, and the honest response is to reconcile and ask for readiness again
 * rather than carry on commanding a runtime nothing was checked against.
 *
 * `credential_superseded` is set when the API said so in those words (a refused press), and when its handshakes say it
 * in theirs: the boot handshake said the API minted the credential itself (`credential_owner: api_process`, before any
 * runtime named a session) and a later one says the API now serves a runtime session's credential
 * (`credential_owner: runtime_session`). The API rotates to that credential when it binds the session
 * (`console_api/app.py` bind), so the value this tab was served is refused from then on (redesign r4 verify-r1
 * must_fix 2). The API serves the credential that belongs to the RUNTIME SESSION, so a restart of the API alone changes
 * nothing here; a replaced runtime does, and the tab is then holding a credential that server has stopped accepting.
 * It is reported, never worked around: the credential reaches a page only in the index.html the API serves, so the
 * way back is a reload and not a retry.
 */
export interface HandshakeState {
  state: 'not_asked' | 'answered' | 'not_answered' | 'incompatible';
  /** The code the handshake came back with when it was not answered: the API's (`runtime_unavailable`) or this page's. */
  reason: string | null;
}

export interface SessionState {
  booted_runtime_session: string | null;
  current_runtime_session: string | null;
  credential_owner: string | null;
  credential_superseded: boolean;
  runtime_replaced: boolean;
  /** Who owned the credential this page was served with, as the BOOT handshake said; null when it did not say. */
  boot_credential_owner: string | null;
  /**
   * Whether the booted session has ANSWERED this tab (a handshake or a snapshot under it). A session named only by the
   * API's `bound_runtime_session` while the runtime was down has not, and nothing was checked against it.
   */
  booted_answered: boolean;
  /**
   * Whose credential this page was served, as the console API's boot block said (the session it was bound to when it
   * rendered the page, and the owner then); null when the page's boot block did not say (redesign r4 verify-r2 must_fix 1).
   */
  served: ServedCredential | null;
}

/**
 * One position the console established on the pad, in PAD_FRAME millimetres, as the run went on. A mark is added ONLY
 * when a decision frame's own perception resolved a pad-frame position; nothing here is interpolated between marks.
 */
export interface SceneMark {
  observation_id: string;
  state: string | null;
  point_mm: [number, number];
  seq: number;
}

export interface ReplayState {
  run_id: string;
  events: ContractEvent[];
  cursor: number;
  /**
   * Which producer session and producer-side sequence number each recorded record came from, by mission seq. The
   * runtime keeps one only for records that have one, so this is not parallel to `events` and is never read by
   * position. Empty for a recording served by a runtime that did not keep them.
   */
  origins: RecordOrigin[];
}

/**
 * The latest telemetry, with WHEN THIS CONSOLE RECEIVED IT on its own clock.
 *
 * The ages inside a sample are measured at the runtime and are frozen the moment the stream stalls: a feed that died
 * would go on reading "captured 44 ms ago" for as long as the tab is open. The received-at stamps are a separate,
 * separately-labelled quantity in the browser's own clock, and they are never subtracted from a sample's own age.
 */
export interface TelemetryState {
  tap: TapSample | null;
  /**
   * The DEFAULT camera's latest sample, kept so a reader that wants one camera keeps working unchanged.
   * `previews` is the full picture: one entry per role, each with its own arrival stamp in
   * `preview_received_at_ms_by_role`, so a camera that stalls cannot clear, freeze or speak for the other.
   */
  preview: PreviewSample | null;
  previews: Record<string, PreviewSample | null>;
  preview_received_at_ms_by_role: Record<string, number | null>;
  tap_received_at_ms: number | null;
  preview_received_at_ms: number | null;
  /**
   * WHEN A NEW FRAME LAST ARRIVED for each role, in this browser's clock, which is a different quantity from
   * `preview_received_at_ms_by_role` above.
   *
   * That one is LINE receipt: the runtime answered about this camera. This one is FRAME receipt: the runtime handed
   * over a picture this tab had not already counted. They were the same stamp, and the runtime heartbeats a
   * frame-less line about once a second while a source is STALLED, so a camera that had stopped delivering read as
   * "just now" for as long as the runtime kept answering. `model/frameProgression.ts` decides which lines count.
   */
  preview_frame_at_ms_by_role: Record<string, number | null>;
  /** The identity of the last frame counted per role, so a duplicate, a reordering or a restart is recognised. */
  preview_frame_identity_by_role: Record<string, FrameIdentity | null>;
  /** Frame arrival times per role in this browser's clock, trimmed to the rate window. Feeds the delivered rate. */
  preview_frame_times_by_role: Record<string, number[]>;
  /**
   * When the FIRST frame of the current source session arrived, per role, in this browser's clock.
   *
   * It is what says the rate window has actually been open long enough for its answer to mean anything, which the
   * trimmed times cannot say by themselves: a handful of closely spaced arrivals looks the same whether the stream
   * opened a moment ago or has been running for an hour. Cleared and restarted when the source session changes,
   * because a new producer restarts the stream and a rate spanning both would describe neither.
   */
  preview_first_frame_at_ms_by_role: Record<string, number | null>;
  /**
   * WHEN THE CURRENT RUN OF ARRIVALS BEGAN, in this browser's clock (`model/arrivalSince.ts`): of tap samples, of the
   * default camera's lines, and of either (the stream). Diagnostics says an arriving feed as the time it began, never the
   * time of its newest sample, which moved every second (browser r2 N4). Absent: not tracked, said without a time.
   */
  tap_since_ms?: number | null;
  preview_since_ms?: number | null;
  line_since_ms?: number | null;
}

export interface StoreSnapshot {
  view: ViewState;
  telemetry: TelemetryState;
  frame: {
    observation_id: string | null;
    record: ObservationRecordDto | null;
    perception: PerceptionDto | null;
    loading: boolean;
    /** Why the page holds no record or perception for it, from the read's own answer; absent before any read ends. */
    record_reason?: string | null;
    perception_reason?: string | null;
  };
  commands: CommandTrack[];
  /**
   * TRUE FROM THE MOMENT THIS TAB SENDS A COMMAND UNTIL A SNAPSHOT READ ASKED FOR AFTER ITS ANSWER HAS ANSWERED (verdict
   * 2026-09-27 must_fix 2). The runtime publishes a run before it answers the command that started it, so until then the
   * capability list on the screen is from before that run: Stop stays on and the other command controls stay off. A
   * refusal started nothing and settles at once; a mission start or retry that was not refused settles the same way.
   */
  commandInFlight: boolean;
  sourceKind: 'live' | 'synthetic';
  sourceLabel: string;
  /** The last error from talking to the source, for the strip. Null when the last exchange worked. */
  sourceError: string | null;
  /** Null until the finished-run list has been read once. A failed read is a failure, not an empty list. */
  history: HistoryResult | null;
  replay: ReplayState | null;
  /**
   * The controller's own output for ONE named run, as the runtime served it, with the reason when it could not be.
   *
   * Bound to a run id and never to "the current run": the whole point of reading it is a run that has ended. It is
   * fetched on demand and never polled, because it does not change once a run has finished.
   */
  runOutput: { run_id: string; stream: 'stdout' | 'stderr'; text: string; total_bytes: number; truncated: boolean } | null;
  /**
   * THE SELECTED RUN'S OWN RECORDS, folded on their own.
   *
   * A tab that opened after a run ended folded no `run.started` and therefore has no start to measure from, and the
   * figures beside the result read unavailable although the runtime had kept every record of that run. This is that
   * run's retained event stream, read back through the existing history path and folded from `initialState()` so
   * nothing of the live session leaks into it and nothing of it leaks into the live view.
   *
   * It supplies PRESENTATION only. Command eligibility is never taken from here: it is a statement about the robot
   * now, and a run from yesterday says nothing about that.
   */
  hydrated: { run_id: string; view: ViewState } | null;
  /**
   * EVERY RUN OF A TARGET THIS PAGE HAS FOLDED, by run id, so one timeline can span a mission's attempts at one target.
   *
   * Each is that run's retained records folded from `initialState()`, read once through the same history path as
   * `hydrated` and never polled: a finished run's records do not change. Bounded to RUN_FOLDS_MAX, the oldest fold
   * dropped first. Presentation only, exactly as `hydrated` is: command eligibility is never read from here.
   */
  runFolds: Record<string, ViewState>;
  hydratedError: string | null;
  hydratingRunId: string | null;
  runOutputError: string | null;
  runOutputLoading: boolean;
  /** Why the last replay could not be opened. A replay is never opened on nothing. */
  replayError: string | null;
  /**
   * What became of the last stop REQUEST. A stop the server refused, or one whose fate is unknown, must be visible:
   * a button that answers with silence is a button an operator will believe worked.
   */
  lastStop: StopAck | null;
  sceneMarks: SceneMark[];
  /**
   * What the last attempt to reconcile the uncertain command came back with. It is kept so the screen can show what
   * the runtime FOUND, which is the part that matters; whether the block is gone is read from the next snapshot and
   * never from this. Null until one has been attempted in this tab.
   */
  lastResolution: ResolutionOutcome | null;
  /** True while a resolve is in flight, so the panel can refuse a second one rather than queue it. */
  resolving: boolean;
  /**
   * The LIVE runtime's unresolved command, kept apart from `view.unresolved`.
   *
   * `view` is the replay fold while a recording is open, and a recording carries no live block, so the block would
   * vanish from the screen the moment somebody opened a replay. It is a fact about the robot in the room and it does
   * not stop being true because a different screen is being looked at.
   */
  liveUnresolved: UnresolvedCommand | null;
  /** Whose runtime this is and whether this tab may still command it. See SessionState. */
  session: SessionState;
  /**
   * THE HANDSHAKE, AS THIS TAB LAST SETTLED IT (redesign r4 verify-r1 must_fix 3): `not_asked` before start, `answered`
   * once the runtime answered one, `not_answered` while the console API answered it for a runtime that did not (its code
   * in `reason`), `incompatible` for a contract this console does not read. Nothing about the runtime (its contract, its
   * commands, its records) is stated while it is not `answered`: it was never read.
   */
  handshake: HandshakeState;
  /** Whether a snapshot read has answered on this display since it opened. Until one has, no runtime record was read. */
  runtimeAnswered: boolean;
  /**
   * The predicate records of the finished run whose result is on the screen, read back from that run's RETAINED
   * evidence when this tab did not fold them live. It is what makes a reloaded or late-opening tab show the same
   * figures as the tab that watched the run end. Null until one has been read. See ResultDetail.
   */
  resultDetail: ResultDetail | null;
  /**
   * What a real Cake resident answered, from the runtime's separately versioned read-only status surface.
   *
   * It is kept OUT of `view` on purpose. `view` is the contract fold, and while a replay is open it is a recording;
   * whether a resident is answering right now is a fact about this machine and does not change because somebody
   * opened a recording. It is also not a contract field, and folding it into the same object would invite exactly
   * the widening of contract v1 that this surface exists to avoid.
   *
   * Null until one read has come back. A read that FAILED sets `cakeStatusError` and leaves the last good status in
   * place, for the same reason the runtime retains its last observation: a resident that stopped answering did not
   * become a resident holding nothing.
   */
  cakeStatus: CakeStatus | null;
  cakeStatusError: string | null;
  /**
   * THE MISSION, AS THE RUNTIME HOLDS IT, and the only place this console keeps one.
   *
   * It is read on the poll and nowhere else, and NOTHING about a mission is kept in this browser: the targets, which
   * one is active, why it is held and every activation it has recorded all come back from the runtime on the next
   * read. That is what makes a reload recover the screen without starting anything and without resubmitting anything.
   *
   * Null until one read has come back, and null also for a runtime carrying no mission, which is `missionError`'s job
   * to say. A read that FAILED leaves the last mission in place, for the same reason a failed Cake read does: a
   * runtime that hiccuped did not become a runtime with no mission.
   */
  mission: MissionRecord | null;
  missionError: string | null;
  /**
   * The runtime's own detail beside `missionError`, when it sent one. For `mission_unavailable` it names which of the
   * runtime's requirements is missing (a live runtime says `rehearsal_requires_synthetic_mode`), and it is what the
   * Start control shows as the reason it is off. Cleared by the next good read.
   */
  missionErrorDetail: string | null;
  /** True while a mission start from this tab is in flight, so a second press is refused rather than queued. */
  startingMission: boolean;
  /** True while a Try again from this tab is in flight, so a second press is refused rather than queued. */
  retryingMission: boolean;
  /** The receipt for the LAST Try again this tab sent. Whether an attempt started is read back from the mission record. */
  lastMissionRetry: MissionStartOutcome | null;
  /**
   * The receipt for the LAST mission start this tab requested. A statement about the request: whether a mission is
   * running is read back from the mission record, which is what survives a reload. Null until this tab has asked.
   */
  lastMissionStart: MissionStartOutcome | null;
  /**
   * The prepared candidates, read only while the mission says it is waiting. Null until one read has come back.
   *
   * The key is the only thing that ever goes back to the runtime, so this list is the whole of what an operator picks
   * from: there is no path by which this console could name a file, a version or an operation instead.
   */
  missionCandidates: MissionCandidate[] | null;
  missionCandidatesError: string | null;
  /** True while an activation is in flight, so a second one is refused rather than queued. */
  activating: boolean;
  /**
   * The receipt for the LAST activation this tab requested. It is a statement about the REQUEST, and never the verdict:
   * the verdict is the activation the runtime recorded in the mission, which is read back and survives a reload. Null
   * until this tab has asked for one.
   */
  lastActivation: ActivationOutcome | null;
  /**
   * When the last GOOD mission read arrived, on this computer's clock. A failed read keeps the last mission and this
   * stamp, so the screen can mark the mission it still shows as retained, with its age, instead of as current.
   */
  missionReadAtMs: number | null;
  /**
   * THE ROBOT'S RESIDENT, from GET /api/robot, read on the same tick as the snapshot. A failed read keeps the last good
   * answer and sets `robotError`; the screen then draws what it keeps as STALE with its age and never as current, and
   * never replaces it with an empty robot. Null until one good read has come back.
   */
  robot: RobotStatus | null;
  robotError: string | null;
  /** When the last GOOD robot read arrived, on this computer's clock. */
  robotReadAtMs: number | null;
  /** When the runtime's snapshots have answered since without a failure or a silence (`model/arrivalSince.ts`). */
  snapshotSinceMs?: number | null;
  /** True while a robot confirmation is in flight, so a second one is refused rather than queued. */
  confirming: boolean;
  /** The receipt for the LAST confirmation this tab sent. The permission itself is read back from GET /api/robot. */
  lastConfirm: RobotConfirmOutcome | null;
  /**
   * THE CAPABILITY SLOT CHECK, from GET /api/capability/check, read on the same tick as the robot. A failed read keeps the
   * last good state and says why beside it; a runtime with no check says so with its reason and detail, which is not an
   * empty check. Null until one good read has come back.
   */
  capabilityCheck: CapabilityCheckState | null;
  capabilityCheckError: string | null;
  /** The runtime's own detail beside a refusal of the read (which requirement of a check is missing). */
  capabilityCheckErrorDetail: string | null;
  /** When the last GOOD check read arrived, on this computer's clock. */
  capabilityCheckReadAtMs: number | null;
  /** True while a check from this tab is in flight, so a second one is refused rather than queued. */
  checking: boolean;
  /** The receipt for the LAST check this tab sent. What it came to is read back from the check's own record. */
  lastCheck: CapabilityCheckOutcome | null;
  /**
   * THE SIGNATURE POLICY CHECK, from GET /api/capability/signature, read on the same tick as the slot check. A failed
   * read keeps the last good state and says why beside it; a runtime with no check says so with its reason and detail.
   * Null until one good read has come back.
   */
  signatureCheck: SignatureCheckState | null;
  signatureCheckError: string | null;
  signatureCheckErrorDetail: string | null;
  /** When the last GOOD signature check read arrived, on this computer's clock. */
  signatureCheckReadAtMs: number | null;
  /** True while a signature check from this tab is in flight, so a second one is refused rather than queued. */
  signatureChecking: boolean;
  /** The receipt for the LAST signature check this tab sent. The table is read back from the check's own record. */
  lastSignatureCheck: SignatureCheckOutcome | null;
  /**
   * EVERY PRESS ON THIS DISPLAY, in order (spec rule 6.5): appended, never overwritten, a pending entry answered in
   * place. It is this display's record, on this display's clock, and it is never sent anywhere.
   */
  presses: PressEntry[];
  /**
   * THE TIMELINE KINDS OF EVERY MISSION RUN, by run id (spec rule 6.3.2): state transitions, refusals, stop records, the
   * end, error and warning lines, and the records the stop's cause joins (planned actions, false predicates, measured
   * states). Followed live while a run is in progress and read once from its retained records when it has finished.
   * Never evicted while the page is open; merged by seq, so a shorter read removes nothing.
   */
  missionRunEvents: Record<string, ContractEvent[]>;
  /** Every change of this display's link to the runtime (lost, reached again), numbered, on this display's clock. */
  links: LinkChange[];
  /**
   * THE TIMELINE (spec rule 6.3): the mission's lines, its runs, its Cake lines, the presses and the link changes,
   * oldest first. A key is inserted once and never removed while the page is open.
   */
  timeline: TimelineEntry[];
  /**
   * THIS DISPLAY'S ACKNOWLEDGEMENTS of mission endings (spec rule 6.2), kept in this browser's storage and never sent to
   * the runtime. A storage that fails leaves this empty after a reload, so the card comes back expanded: the safe side.
   */
  acknowledgements: AckRecord[];
  /**
   * THE MISSIONS THIS PAGE READ WHILE THEY HAD NOT ENDED, by id, in the order first seen (spec section 12, rule 3): a
   * mission the operator watched stays on the main screen through its end, whatever session or day it began in. This
   * page's own observation, kept for the page's life and never sent anywhere; a reload starts it empty, and the session
   * rule then decides from the record alone.
   */
  missionsSeenOpen: string[];
  /**
   * EVERY MISSION THIS PAGE READ, the newest record of each, in the order first seen (spec section 12; verify-r1 M1). The
   * timeline is append-only, so the lines of a mission read earlier stay in it after the runtime serves another one; the
   * screen reads this list to keep a mission that is History's off the main screen, and History to keep carrying it. This
   * page's own observation, kept for the page's life and never sent anywhere; a reload starts it with what is served.
   */
  missionsRead: MissionRecord[];
}

/** The part of the browser's storage the store uses: two calls, each of which may throw. */
export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface StoreOptions {
  /** Where acknowledgements are kept; the browser's localStorage when absent, none when there is no browser. */
  storage?: KeyValueStorage | null;
}

function defaultStorage(): KeyValueStorage | null {
  try {
    const candidate = (globalThis as { localStorage?: KeyValueStorage }).localStorage;
    return candidate === undefined ? null : candidate;
  } catch {
    return null;
  }
}

function readAcknowledgements(storage: KeyValueStorage | null): AckRecord[] {
  if (storage === null) return [];
  try {
    const parsed = JSON.parse(storage.getItem(ACKNOWLEDGED_KEY) ?? '[]') as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is AckRecord => typeof item === 'object' && item !== null && typeof (item as AckRecord).key === 'string');
  } catch {
    return [];
  }
}

/** How many folded runs `runFolds` keeps. Two targets with a few attempts each fit; the oldest goes first. */
export const RUN_FOLDS_MAX = 8;

/**
 * True when an ANSWERED handshake says the console API is bound to another runtime session than the one it served this
 * page's credential for (`api/boot.ts` ServedCredential). The API rotates its credential exactly when it binds another
 * session (`console_api/app.py` bind), so the value this page holds is then refused `credential_superseded`. False when
 * the boot block did not say, when the handshake was not answered, and when the API does not own its credential per
 * runtime session (it then never rotates).
 */
function rotatedSinceServed(served: ServedCredential | null, hello: HelloResult): boolean {
  if (served === null || !hello.ok || hello.credential_owner !== 'runtime_session') return false;
  const bound = hello.bound_runtime_session;
  return typeof bound === 'string' && bound !== '' && bound !== served.runtime_session;
}

export class ConsoleStore {
  private listeners = new Set<() => void>();
  private snapshot: StoreSnapshot;
  private liveView: ViewState = initialState();
  private source: ConsoleSource;
  private unsubscribeEvents: Unsubscribe | null = null;
  private unsubscribeTelemetry: Unsubscribe | null = null;
  private poll: ReturnType<typeof setInterval> | null = null;
  /** The runs `foldRuns` is reading now, so a run is asked for once while a read is in flight. */
  private foldingRuns = new Set<string>();
  /** Guards hydrateRun: only the newest request may publish its answer. */
  private hydrateToken = 0;
  /** The re-read of the selected run's read-back fold (`followSelectedRunRecords`): in flight, asked again, and for which state. */
  private selectedRunReading = false;
  private selectedRunReadAgain = false;
  private selectedRunReadKey: string | null = null;
  /** Robot reads, numbered as they are sent, and the newest one whose answer was applied: an older answer never lands. */
  private robotReadsSent = 0;
  private robotReadApplied = 0;
  private frameToken = 0;
  private started = false;
  /** The telemetry that has arrived and has not been published yet. Latest-only, like the samples themselves. */
  private pendingTelemetry: Partial<TelemetryState> | null = null;
  private telemetryFlush: ReturnType<typeof setTimeout> | null = null;
  private telemetryPublishedAtMs = 0;
  /** The run a result-detail read is in flight for, so the same run is asked for once and not on every publish. */
  private resultDetailFor: string | null = null;
  /**
   * The mission whose candidate list has already been read while it was waiting.
   *
   * The list is read ONCE per waiting episode rather than on every poll. A list that was re-fetched every five seconds
   * would change under the operator's finger between choosing a candidate and pressing the button, and there is no
   * reason to: a prepared candidate is prepared.
   */
  private candidatesFor: string | null = null;
  /** The mission runs whose retained records were read with their end, and the ones being read now. */
  private missionRunsRead = new Set<string>();
  private missionRunsReading = new Set<string>();
  /** True while no handshake has been answered by the runtime; the next snapshot read asks it again first. */
  private helloOutstanding = false;
  /** How many handshakes this store has applied; the first is the boot handshake, whose credential owner is kept. */
  private hellosApplied = 0;
  /** The code commands were turned off with because the handshake was not answered, so an answered one lifts only that. */
  private helloDisabledReason: string | null = null;
  /** The one mission read scheduled after a run's end (`rereadMissionAfterRunEnd`), or null. */
  private missionReread: ReturnType<typeof setTimeout> | null = null;
  /** The one snapshot read scheduled after a run's end or a mission's end (`rereadSnapshotAfterEnd`), or null. */
  private snapshotReread: ReturnType<typeof setTimeout> | null = null;
  /** How many more of those reads the newest end allows (`SNAPSHOT_REREADS_AFTER_END` at each end). */
  private snapshotRereadsLeft = 0;
  /** The one mission read scheduled after an observed activation (`rereadMissionAfterActivation`), or null. */
  private activationReread: ReturnType<typeof setTimeout> | null = null;
  /** How many more of those reads the newest activation allows (`MISSION_REREADS_AFTER_ACTIVATION` at each answer). */
  private activationRereadsLeft = 0;
  /** Why the last handshake was not answered, in the API's code, for the link sentence. */
  private helloReason: string | null = null;
  /** True while a finished-run list read is in flight, so a recovery asks for it once. */
  private historyReading = false;
  /** Commands sent from this tab whose answer is not in yet. */
  private submitsInFlight = 0;
  /** How many snapshot reads this store has sent; a read's number is taken as it is sent. */
  private snapshotAsks = 0;
  /**
   * The number of the last snapshot read sent when the newest answer that may have started a run came in, or null. A
   * read sent after it is the first that can show that run; until one answers, `commandInFlight` stays true.
   */
  private settleAfterAsk: number | null = null;
  /**
   * THE RUN IN PROGRESS whose earlier records were read back into the live fold (`followActiveRun`), the one being read
   * now, and the snapshot a read that did not answer was asked under, so it is asked again once per snapshot, not per event.
   */
  private activeRunRead: string | null = null;
  private activeRunReading: string | null = null;
  private activeRunAskedAt: { run_id: string; snapshot_at_ms: number | null } | null = null;

  private readonly storage: KeyValueStorage | null;

  constructor(source: ConsoleSource, options: StoreOptions = {}) {
    this.source = source;
    this.storage = options.storage === undefined ? defaultStorage() : options.storage;
    this.snapshot = {
      view: this.liveView,
      telemetry: {
        tap: null, preview: null, previews: {}, preview_received_at_ms_by_role: {}, tap_received_at_ms: null, preview_received_at_ms: null,
        preview_frame_at_ms_by_role: {}, preview_frame_identity_by_role: {}, preview_frame_times_by_role: {},
        preview_first_frame_at_ms_by_role: {},
      },
      frame: { observation_id: null, record: null, perception: null, loading: false },
      commands: [],
      commandInFlight: false,
      sourceKind: source.kind,
      sourceLabel: source.label,
      sourceError: null,
      history: null,
      replay: null,
      replayError: null,
      runOutput: null,
      runOutputError: null,
      runOutputLoading: false,
      hydrated: null,
      runFolds: {},
      hydratedError: null,
      hydratingRunId: null,
      lastStop: null,
      sceneMarks: [],
      lastResolution: null,
      resolving: false,
      liveUnresolved: null,
      resultDetail: null,
      cakeStatus: null,
      cakeStatusError: null,
      mission: null,
      missionError: null,
      missionErrorDetail: null,
      startingMission: false,
      lastMissionStart: null,
      retryingMission: false,
      lastMissionRetry: null,
      missionCandidates: null,
      missionCandidatesError: null,
      activating: false,
      lastActivation: null,
      missionReadAtMs: null,
      robot: null,
      robotError: null,
      robotReadAtMs: null,
      confirming: false,
      lastConfirm: null,
      capabilityCheck: null,
      capabilityCheckError: null,
      capabilityCheckErrorDetail: null,
      capabilityCheckReadAtMs: null,
      checking: false,
      lastCheck: null,
      signatureCheck: null,
      signatureCheckError: null,
      signatureCheckErrorDetail: null,
      signatureCheckReadAtMs: null,
      signatureChecking: false,
      lastSignatureCheck: null,
      presses: [],
      missionRunEvents: {},
      links: [],
      timeline: [],
      acknowledgements: readAcknowledgements(this.storage),
      missionsSeenOpen: [],
      missionsRead: [],
      session: {
        booted_runtime_session: null,
        current_runtime_session: null,
        credential_owner: null,
        credential_superseded: false,
        runtime_replaced: false,
        boot_credential_owner: null,
        booted_answered: false,
        served: source.servedCredential ?? null,
      },
      handshake: { state: 'not_asked', reason: null },
      runtimeAnswered: false,
    };
  }

  private pressCount = 0;

  /** Register one press on this display, pending; the answer comes with `settlePress`. Returns the entry's id. */
  beginPress(control: PressControl): string {
    this.pressCount += 1;
    const id = `press-${String(this.pressCount)}`;
    const entry: PressEntry = {
      id,
      control,
      pressed_at: new Date().toISOString(),
      answer: 'pending',
      text: null,
      answered_at: null,
      mission_id: this.snapshot.mission?.mission_id ?? null,
      run_id: this.snapshot.view.active_run?.run_id ?? null,
      record: null,
    };
    this.emit({ presses: appendPress(this.snapshot.presses, entry) });
    return id;
  }

  /** Give a pending press its receipt, once. */
  settlePress(id: string, receipt: Receipt, record: string | null = null): void {
    this.emit({ presses: answerPress(this.snapshot.presses, id, { answer: receipt.tone, text: receipt.text, answered_at: new Date().toISOString(), record }) });
  }

  /**
   * ACKNOWLEDGE ONE MISSION ENDING ON THIS DISPLAY (spec rule 6.2). It is recorded in the press list and in this
   * browser's storage, and it is NEVER sent to the runtime: there is no such route, and it changes the card only.
   */
  acknowledge(record: Omit<AckRecord, 'acknowledged_at'>): void {
    const press = this.beginPress('acknowledge');
    const entry: AckRecord = { ...record, acknowledged_at: new Date().toISOString() };
    const next = [...this.snapshot.acknowledgements.filter((item) => item.key !== entry.key), entry];
    this.emit({ acknowledgements: next });
    try {
      this.storage?.setItem(ACKNOWLEDGED_KEY, JSON.stringify(next.slice(-50)));
    } catch {
      /* the card comes back expanded after a reload: the safe side */
    }
    this.settlePress(press, { tone: 'taken', text: `Acknowledged mission ${record.mission_id} on this display. Nothing was sent to the runtime.` });
  }

  /** A press this display refused at the click, before anything was sent: registered and answered at once. */
  refusedPress(control: PressControl, text: string): void {
    this.settlePress(this.beginPress(control), { tone: 'refused', text });
  }

  subscribe = (listener: () => void): Unsubscribe => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): StoreSnapshot => this.snapshot;

  private emit(patch: Partial<StoreSnapshot>): void {
    const linkWas = this.snapshot.sourceError;
    // A LINK CHANGE IS THIS DISPLAY'S OWN OBSERVATION of a runtime it had reached (verify-r1 must_fix 4). A display that
    // never had an answer did not lose the runtime and does not reach it "again": it records no change until it has.
    const answeredBefore = this.snapshot.runtimeAnswered;
    this.snapshot = { ...this.snapshot, ...patch };
    if ('sourceError' in patch && answeredBefore && (linkWas === null) !== (this.snapshot.sourceError === null)) {
      const n = this.snapshot.links.length + 1;
      this.snapshot = { ...this.snapshot, links: [...this.snapshot.links, { n, at: new Date().toISOString(), lost: this.snapshot.sourceError !== null }] };
    }
    if (TIMELINE_INPUTS.some((key) => key in patch)) {
      const next = composeTimeline({
        mission: this.snapshot.mission,
        history: this.snapshot.history?.ok === true ? this.snapshot.history.entries : [],
        runEvents: this.snapshot.missionRunEvents,
        presses: this.snapshot.presses,
        links: this.snapshot.links,
        candidates: this.snapshot.missionCandidates,
        today: new Date().toISOString().slice(0, 10),
        // A RUNTIME SESSION THAT DATES ITSELF prints every time off today with its date (spec section 12, rule 5.4).
        datedDay: runtimeSessionStart(this.snapshot.view.runtime_session) === null ? null : new Date().toISOString().slice(0, 10),
      });
      this.snapshot = { ...this.snapshot, timeline: mergeTimeline(this.snapshot.timeline, next) };
    }
    for (const listener of this.listeners) listener();
  }

  /** Keep the timeline kinds of these events, by run, merged by seq with what is already kept. */
  private keepRunEvents(events: readonly ContractEvent[]): void {
    const byRun: Record<string, ContractEvent[]> = {};
    for (const event of events) {
      if (event.run_id === null || !keptEvent(event)) continue;
      (byRun[event.run_id] ??= []).push(event);
    }
    const runs = Object.keys(byRun);
    if (runs.length === 0) return;
    const next = { ...this.snapshot.missionRunEvents };
    for (const runId of runs) {
      const merged = new Map<number, ContractEvent>();
      for (const event of next[runId] ?? []) merged.set(event.seq, event);
      for (const event of byRun[runId] ?? []) merged.set(event.seq, event);
      next[runId] = [...merged.values()].sort((a, b) => a.seq - b.seq);
    }
    this.emit({ missionRunEvents: next });
  }

  /**
   * READ EACH FINISHED MISSION RUN ONCE, from its retained records. A run is read again only while its read did not
   * include its end, so a run that was still going when it was asked for is asked for again later. A READ.
   */
  private async readMissionRuns(): Promise<void> {
    const mission = this.snapshot.mission;
    if (mission === null) return;
    // THE RUN IN PROGRESS BY THE RECORD (verify-r1 must_fix 5, 6): a run whose end the live fold holds is read with its end
    // at the next mission read, not left until the snapshot stops naming it.
    const active = activeRunOnRecord(this.liveView);
    for (const runId of mission.targets.flatMap((target) => target.runs)) {
      if (runId === active || this.missionRunsRead.has(runId) || this.missionRunsReading.has(runId)) continue;
      this.missionRunsReading.add(runId);
      try {
        const answer = await this.source.historyEvents(runId);
        if (!answer.ok) continue;
        this.keepRunEvents(answer.events);
        if (answer.events.some((event) => event.kind === 'run.finished')) this.missionRunsRead.add(runId);
      } catch {
        /* read again on the next mission read */
      } finally {
        this.missionRunsReading.delete(runId);
      }
    }
    // THE MISSION'S END IS ITS LAST RUN'S FINISH, which only the finished-run list carries (spec rule 6.7). A list read
    // just as a run finished can predate it; while a finished mission run is missing from it, it is read again.
    if (this.missionRunMissingFromHistory()) await this.refreshHistory();
  }

  /** The view the screen draws: the folded replay when one is open, otherwise the live fold. */
  private publishView(): void {
    const replay = this.snapshot.replay;
    const view =
      replay === null ? this.liveView : foldEvents(initialState(), replay.events.slice(0, replay.cursor));
    // A new run clears the pad scene: the marks of the previous run are not this run's evidence.
    // `liveUnresolved` is taken from the LIVE fold on every publish, replay or not: see StoreSnapshot.
    const liveUnresolved = this.liveView.unresolved;
    if (view.machine === null && this.snapshot.sceneMarks.length > 0) this.emit({ view, sceneMarks: [], liveUnresolved });
    else this.emit({ view, liveUnresolved });
    this.followDecisionFrame(view, replay === null ? this.readBackFrameView(view) : null);
    this.followResultDetail(view);
    this.followActiveRun();
    if (replay === null) this.followSelectedRunRecords();
  }

  /**
   * THE READ-BACK FOLD OF THE RUN ON SCREEN IS READ AGAIN WHILE IT DOES NOT REACH THE RUN'S END AND THE RECORD HAS MOVED
   * PAST IT (round 7 item 4; verify-r6 closure M1, honesty M1, browser walk M1). After a runtime replacement the screen
   * draws the selected run from its read-back fold (`viewForSelectedRun`), which was read once, mid-run, and never again,
   * so an adopted run froze on "In progress" until a reload. It is read again (the same read a fresh tab makes) when the
   * run's records kept by this tab go past it, when the snapshot says the run is no longer active or names its end, or
   * when another runtime session answers; at most one read in flight, and never twice for the same state of the record.
   * A fold that holds the run's end is final and is not read again.
   */
  private followSelectedRunRecords(): void {
    const held = this.snapshot.hydrated;
    if (held === null || held.view.outcome.run_id === held.run_id && held.view.outcome.reported !== null) return;
    const live = this.liveView;
    const kept = this.snapshot.missionRunEvents[held.run_id] ?? [];
    const keptNewest = kept.length === 0 ? null : (kept[kept.length - 1] as ContractEvent).seq;
    const readUpTo = held.view.last_event_seq ?? 0;
    const endNamed = live.outcome.run_id === held.run_id && live.outcome.reported !== null;
    const notActive = live.active_run?.run_id !== held.run_id;
    const moved = (keptNewest !== null && keptNewest > readUpTo) || endNamed || notActive;
    if (!moved) return;
    const key = `${live.runtime_session ?? ''}|${String(live.seq_cursor)}|${String(keptNewest)}|${String(endNamed)}|${String(notActive)}`;
    if (key === this.selectedRunReadKey) return;
    if (this.selectedRunReading) {
      this.selectedRunReadAgain = true;
      return;
    }
    this.selectedRunReadKey = key;
    void this.rereadSelectedRun(held.run_id);
  }

  private async rereadSelectedRun(runId: string): Promise<void> {
    this.selectedRunReading = true;
    try {
      const answer = await this.source.historyEvents(runId);
      if (!answer.ok || this.snapshot.hydrated?.run_id !== runId) return;
      const folded = foldEvents(initialState(), answer.events);
      // Never back: a read that reaches less far than the fold held is not taken for the run's newer state.
      if ((folded.last_event_seq ?? 0) < (this.snapshot.hydrated.view.last_event_seq ?? 0)) return;
      this.keepRunEvents(answer.events);
      this.emit({ hydrated: { run_id: runId, view: folded }, runFolds: this.withRunFold(runId, folded) });
    } catch {
      /* read again when the record moves */
    } finally {
      this.selectedRunReading = false;
    }
    const again = this.selectedRunReadAgain;
    this.selectedRunReadAgain = false;
    if (again) this.selectedRunReadKey = null;
    this.publishView();
  }

  /**
   * READ BACK THE EARLIER RECORDS OF THE RUN IN PROGRESS, when this tab's stream will never deliver them (redesign verdict
   * item 5). A tab opened mid-run (a reload, a second display) subscribes from the snapshot's cursor, past the run's
   * start; the runtime serves every record of the run, and they are merged into the live fold and the kept run events by
   * seq, so the tab draws the run as the tab that watched it does. A READ, once per run: the stream carries it on from
   * there. It waits while the stream has not yet reached the run's start, because then the stream delivers it in order.
   */
  private followActiveRun(): void {
    const live = this.liveView;
    const active = live.active_run;
    if (active === null || live.run_started?.run_id === active.run_id) return;
    if (this.activeRunRead === active.run_id || this.activeRunReading === active.run_id) return;
    const startedSeq = active.started_seq ?? null;
    if (live.last_event_seq !== null && startedSeq !== null && live.last_event_seq < startedSeq) return;
    const asked = this.activeRunAskedAt;
    if (asked !== null && asked.run_id === active.run_id && asked.snapshot_at_ms === live.snapshot_at_ms) return;
    this.activeRunAskedAt = { run_id: active.run_id, snapshot_at_ms: live.snapshot_at_ms };
    void this.readActiveRun(active.run_id);
  }

  private async readActiveRun(runId: string): Promise<void> {
    this.activeRunReading = runId;
    try {
      const answer = await this.source.historyEvents(runId);
      if (!answer.ok || !answer.events.some((event) => event.kind === 'run.started')) return;
      this.activeRunRead = runId;
      this.keepRunEvents(answer.events);
      this.liveView = mergeRunRecords(this.liveView, answer.events);
      this.publishView();
    } catch {
      /* asked again with the next snapshot */
    } finally {
      if (this.activeRunReading === runId) this.activeRunReading = null;
    }
  }

  /**
   * Read back the predicate records of the finished run on the screen, when this tab did not fold them live.
   *
   * A tab that opened after a run ended folded none of that run's records, so the measurement behind a predicate would
   * be missing from the screen while it sits in the retained run the whole time. The runtime already serves those
   * records per run, and they are folded HERE BY THE SAME REDUCER the live path uses, so a read-back result cannot
   * drift from a watched one.
   *
   * It stays out of REPLAY: a recording is a different mode with its own fold, and mixing the two would put one run's
   * evidence under another run's badge. It is scoped to the run id, asked once per run, and never retried into a loop.
   */
  private followResultDetail(view: ViewState): void {
    if (this.snapshot.replay !== null) return;
    const runId = resultForCurrentRun(view)?.run_id ?? null;
    if (runId === null) {
      if (this.snapshot.resultDetail !== null) this.emit({ resultDetail: null });
      return;
    }
    if (this.snapshot.resultDetail?.run_id === runId || this.resultDetailFor === runId) return;
    // Already folded live, by the shorter path to the same records. Nothing to read back.
    if (view.predicates.some((item) => item.predicate === 'clearance_target_met')) return;
    this.resultDetailFor = runId;
    void this.readResultDetail(runId);
  }

  private async readResultDetail(runId: string): Promise<void> {
    // An older run that never retained these records, or one whose records cannot be read, stays UNAVAILABLE. The
    // screen then says the measurement is not there, which is true, rather than borrowing one from anywhere.
    let detail: ResultDetail = { run_id: runId, predicates: [], state: 'unavailable' };
    try {
      const answer = await this.source.historyEvents(runId);
      if (answer.ok) {
        const folded = foldEvents(initialState(), answer.events);
        if (folded.predicates.length > 0) detail = { run_id: runId, predicates: folded.predicates, state: 'retained' };
      }
    } catch {
      /* stays unavailable */
    }
    // The run on the screen may have moved on while this was in flight; a stale answer is never published.
    if (resultForCurrentRun(this.snapshot.view)?.run_id === runId) this.emit({ resultDetail: detail });
    if (this.resultDetailFor === runId) this.resultDetailFor = null;
  }

  /**
   * WHAT ONE HANDSHAKE SETTLED. Only the API's `contract_incompatible`, or a contract version other than this console's,
   * makes the tab read-only for good. A handshake the RUNTIME did not answer (the API's `runtime_unavailable`, this page's
   * `runtime_unreachable`, an answer that could not be read) is not a contract this console cannot read and says nothing
   * about one: it is a runtime that has not answered yet. It leaves the handshake outstanding, and `refreshSnapshot` asks
   * it again before it counts the runtime as answering (verdict 2026-09-27 must_fix 1).
   */
  private applyHello(hello: HelloResult): 'incompatible' | 'unanswered' | 'answered' {
    const session = this.snapshot.session;
    const boot = this.hellosApplied === 0;
    this.hellosApplied += 1;
    // THE SESSION THIS TAB'S CREDENTIAL BELONGS TO: the runtime's, or, when the runtime did not answer, the one the API
    // says it is bound to. A runtime that comes back under another session has then replaced it (`noteRuntimeSession`).
    const named = hello.runtime_session ?? (hello.ok ? null : (hello.bound_runtime_session ?? null));
    // Who owned the credential when the page was served: the boot block's word when it said, else the boot handshake's.
    const bootOwner = boot ? (session.served?.owner ?? hello.credential_owner ?? null) : session.boot_credential_owner;
    // A CREDENTIAL THE CONSOLE API MINTED FOR ITSELF IS SUPERSEDED once the API serves a runtime session's (verify-r1
    // must_fix 2): the boot handshake said `api_process` and this one says `runtime_session`. That session never answered
    // this tab and its credential is not the one this page holds, so it is not adopted as the one this tab booted with.
    // THE SAME IS TRUE OF A CREDENTIAL SERVED FOR ANOTHER SESSION (verify-r2 must_fix 1): the boot block names the session
    // the page's credential belongs to, and an answered handshake that says the API is bound to another one says the API
    // has rotated since it served this page. That can be the page's own first handshake, when the runtime was replaced
    // with no request to the API in between, so it is read at boot as at every later handshake.
    const servedSuperseded = rotatedSinceServed(session.served, hello);
    const superseded = servedSuperseded || (!boot && hello.ok && bootOwner === 'api_process' && hello.credential_owner === 'runtime_session');
    const adopt = !(session.credential_superseded || superseded);
    const booted = adopt ? (session.booted_runtime_session ?? named) : session.booted_runtime_session;
    this.emit({
      session: {
        ...session,
        booted_runtime_session: booted,
        current_runtime_session: superseded ? named : (session.current_runtime_session ?? named),
        credential_owner: hello.credential_owner ?? session.credential_owner,
        boot_credential_owner: bootOwner,
        booted_answered: session.booted_answered || (hello.ok && hello.runtime_session !== null && hello.runtime_session === booted),
        credential_superseded: session.credential_superseded || superseded,
        runtime_replaced: session.runtime_replaced || superseded,
      },
    });
    if (hello.reason_code === 'contract_incompatible' || (hello.contract_version !== null && hello.contract_version !== '1')) {
      this.helloOutstanding = false;
      this.emit({ handshake: { state: 'incompatible', reason: 'contract_incompatible' } });
      this.liveView = markContractIncompatible(this.liveView, 'contract_incompatible');
      this.publishView();
      return 'incompatible';
    }
    if (!hello.ok) {
      this.helloOutstanding = true;
      this.helloReason = hello.reason_code ?? 'the handshake was not answered';
      this.emit({ handshake: { state: 'not_answered', reason: this.helloReason } });
      // COMMANDS ARE WHAT THE API ANSWERED (verify-r1 must_fix 3): off, with its code, until a handshake is answered. The
      // default of the fold is not an answer. Stop never reads this (`model/failureBanner.stopOffReason`).
      if (this.liveView.commands_enabled) {
        this.helloDisabledReason = hello.reason_code ?? 'handshake_not_answered';
        this.liveView = markCommandsDisabled(this.liveView, this.helloDisabledReason);
        this.publishView();
      }
      return 'unanswered';
    }
    this.helloOutstanding = false;
    this.helloReason = null;
    this.emit({ handshake: { state: 'answered', reason: null } });
    if (this.helloDisabledReason !== null) {
      // Only the handshake's own refusal is lifted; a reason set since (a replaced runtime) stays.
      if (!this.liveView.commands_enabled && this.liveView.read_only_reason === this.helloDisabledReason) {
        this.liveView = { ...this.liveView, commands_enabled: true, read_only_reason: null };
        this.publishView();
      }
      this.helloDisabledReason = null;
    }
    if (!hello.commands_enabled) {
      // The contract is fine and the record keeps arriving; this tab just may not command. Following continues.
      this.liveView = markCommandsDisabled(this.liveView, hello.reason_code);
      this.publishView();
    }
    if (this.snapshot.session.credential_superseded && this.liveView.read_only_reason !== 'credential_superseded') {
      // Before any press: every command from this tab would be refused by the console server, Stop included.
      this.liveView = markCommandsDisabled(this.liveView, 'credential_superseded');
      this.publishView();
    }
    return 'answered';
  }

  /**
   * FOLLOW THE RECORD STREAM FROM `since`. Opened once at start, and again when a snapshot names another runtime session
   * than the fold's (round 7 item 4): a stream to a daemon that died without closing it can stay open and deliver
   * nothing, and the new daemon's records (an adopted run's rest and its end) then never reach this tab. It reopens from
   * the cursor of the snapshot that named the new session, as a fresh tab subscribes; the records of the run on screen
   * before that cursor are read back from the run's retained events (`followSelectedRunRecords`).
   */
  private openEventStream(since: number): void {
    this.unsubscribeEvents?.();
    this.unsubscribeEvents = this.source.subscribeEvents(
      since,
      (message) => {
        if ('event' in message) {
          this.liveView = applyEvent(this.liveView, message.event);
          this.keepRunEvents([message.event]);
          // A run has ended, so the finished-run list on screen is out of date. Without this the panel would go on
          // saying "no run has finished in this runtime session" after one just did.
          if (message.event.kind === 'run.finished') void this.refreshHistory();
          // A RUN STARTED OR ENDED: the mission record (which lists the run and says what the mission did at its end) and
          // the snapshot (whose active run and list name it) are read again now, not left to the next poll (verify-r1
          // must_fix 5 and 6). Reads only.
          if (message.event.kind === 'run.started' || message.event.kind === 'run.finished') {
            const ended = message.event.kind === 'run.finished';
            void this.refreshMission().then(() => {
              if (ended) this.rereadMissionAfterRunEnd();
            });
            if (ended) this.snapshotRereadsLeft = SNAPSHOT_REREADS_AFTER_END;
            void this.refreshSnapshot().then(() => {
              if (ended) this.rereadSnapshotAfterEnd();
            });
          }
        } else if ('resync' in message) this.liveView = applyResync(this.liveView, message.resync);
        else if ('dropped' in message) this.liveView = markDropNotice(this.liveView);
        else this.liveView = markMalformedLine(this.liveView);
        this.publishView();
      },
      () => {
        this.liveView = markReconnect(this.liveView);
        this.publishView();
      },
    );
  }

  /** Follow nothing more: a tab whose runtime speaks another contract is read-only and asks for nothing. */
  private stopFollowing(): void {
    if (this.poll !== null) clearInterval(this.poll);
    this.poll = null;
    this.unsubscribeEvents?.();
    this.unsubscribeEvents = null;
    this.unsubscribeTelemetry?.();
    this.unsubscribeTelemetry = null;
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    const hello = await this.source.hello();
    if (this.applyHello(hello) === 'incompatible') return; // read-only: follow nothing.

    await this.refreshSnapshot();
    await this.refreshCakeStatus();
    // A READ, on the way in, like the mission below: which robot, whether its resident and host are there, and whether
    // motion is held. Nothing is confirmed here or anywhere on mount; a confirmation is only ever an operator's click.
    await this.refreshRobot();
    // A READ, on the way in: the capability slot check's candidates, the reading it would act on and its record. Nothing
    // is applied here or anywhere on mount; a check is only ever an operator's click.
    await this.refreshCapabilityCheck();
    // A READ, on the way in: the signature policy check's record. Nothing is run here or anywhere on mount.
    await this.refreshSignatureCheck();
    // A READ, on the way in. It is what a reloaded tab recovers the mission from, and it is the reason nothing about a
    // mission has to be kept in this browser. Nothing is started and nothing is activated here or anywhere on mount.
    await this.refreshMission();
    this.poll = setInterval(() => {
      void this.refreshSnapshot();
      // The SAME tick, not a timer of its own. The runtime holds its resident reading for a poll interval and answers
      // this from that held value, so asking here costs no extra child process; a second timer would only make the
      // two facts on the screen disagree about when they were read.
      void this.refreshCakeStatus();
      void this.refreshRobot();
      void this.refreshCapabilityCheck();
      void this.refreshSignatureCheck();
      void this.refreshMission();
      if (this.watchedScanMissingFromHistory()) void this.refreshHistory();
    }, SNAPSHOT_POLL_MS);

    this.openEventStream(this.liveView.seq_cursor);

    this.unsubscribeTelemetry = this.source.subscribeTelemetry((message) => {
      // The arrival stamp is this computer's clock and is kept apart from every age inside the sample. It is taken
      // HERE, when the line arrived, and never when the sample is published: a sample that waited for the window to
      // close did not arrive when the window closed. Receipt does not wait for React.
      const at = Date.now();
      if ('tap' in message) {
        const pendingTap = this.pendingTelemetry ?? {};
        const lastTap = pendingTap.tap_received_at_ms !== undefined ? pendingTap.tap_received_at_ms : this.snapshot.telemetry.tap_received_at_ms;
        this.queueTelemetry({
          tap: message.tap,
          tap_received_at_ms: at,
          tap_since_ms: arrivalSince(pendingTap.tap_since_ms ?? this.snapshot.telemetry.tap_since_ms ?? null, lastTap, at, TELEMETRY_SILENT_AFTER_MS),
          line_since_ms: this.lineSince(at),
        });
        return;
      }
      // A preview line belongs to ONE camera. Its sample and its arrival stamp are filed under that role, and the
      // default role's values are kept beside them for readers that want a single camera.
      const sample = previewSampleOf(message);
      const role = previewRoleOf(message, sample);
      if (sample === null || role === null) return;
      const current = this.pendingTelemetry ?? {};
      const previews = { ...(current.previews ?? this.snapshot.telemetry.previews), [role]: sample };
      const stamps = { ...(current.preview_received_at_ms_by_role ?? this.snapshot.telemetry.preview_received_at_ms_by_role), [role]: at };
      const patch: Partial<TelemetryState> = { previews, preview_received_at_ms_by_role: stamps };

      // A LINE IS NOT A FRAME. The stamps above say the runtime answered about this camera; the three below only move
      // when it handed over a picture this tab had not already counted. Scoped per role: the two cameras are numbered
      // independently and a frame of one can never speak for the other.
      const identities = current.preview_frame_identity_by_role ?? this.snapshot.telemetry.preview_frame_identity_by_role;
      const identity = frameIdentityOf(sample);
      if (isNewFrame(identities[role] ?? null, identity)) {
        const times = current.preview_frame_times_by_role ?? this.snapshot.telemetry.preview_frame_times_by_role;
        const firsts = current.preview_first_frame_at_ms_by_role ?? this.snapshot.telemetry.preview_first_frame_at_ms_by_role;
        // A NEW PRODUCER RESTARTS THE MEASUREMENT. Frames from the session before it were delivered by a stream that
        // no longer exists, so they are dropped rather than averaged with the new one's: a rate spanning a restart
        // describes neither side of it. Only a session BOTH sides state can decide this, which is the same rule
        // `isNewFrame` applies before it looks at any counter.
        const previousSession = (identities[role] ?? null)?.session ?? null;
        const nextSession = identity?.session ?? null;
        const restarted = previousSession !== null && nextSession !== null && previousSession !== nextSession;
        const kept = restarted ? [] : (times[role] ?? []);
        patch.preview_frame_identity_by_role = { ...identities, [role]: identity };
        patch.preview_frame_at_ms_by_role = { ...(current.preview_frame_at_ms_by_role ?? this.snapshot.telemetry.preview_frame_at_ms_by_role), [role]: at };
        patch.preview_frame_times_by_role = { ...times, [role]: withFrameAt(kept, at) };
        const firstAt = firsts[role] ?? null;
        patch.preview_first_frame_at_ms_by_role = { ...firsts, [role]: restarted || firstAt === null ? at : firstAt };
      }

      if (role === DEFAULT_PREVIEW_ROLE) {
        const lastPreview = current.preview_received_at_ms !== undefined ? current.preview_received_at_ms : this.snapshot.telemetry.preview_received_at_ms;
        patch.preview_since_ms = arrivalSince(current.preview_since_ms ?? this.snapshot.telemetry.preview_since_ms ?? null, lastPreview, at, TELEMETRY_SILENT_AFTER_MS);
        patch.line_since_ms = this.lineSince(at);
        patch.preview = sample;
        patch.preview_received_at_ms = at;
      }
      this.queueTelemetry(patch);
    });

    void this.refreshHistory();
  }

  /**
   * ONE MORE READ OF THE MISSION A SECOND AFTER A RUN ENDED, while the record read at the end still says RUNNING with no
   * run in progress by the record (verify-r1 must_fix 6). The runtime writes what the mission did at a run's end (the next
   * attempt, a wait, an end) just after the run's own end, and the card says it is reading the mission record meanwhile;
   * this makes that true sooner than the next poll. A READ, once per run end.
   */
  private rereadMissionAfterRunEnd(): void {
    if (this.missionReread !== null || this.snapshot.mission?.state !== 'RUNNING' || activeRunOnRecord(this.liveView) !== null) return;
    this.missionReread = setTimeout(() => {
      this.missionReread = null;
      void this.refreshMission();
    }, MISSION_REREAD_AFTER_RUN_END_MS);
  }

  /**
   * THE MISSION READ AGAIN 250 MS APART, FOR AT MOST 2 S, WHILE THE RECORD READ AFTER AN ACTIVATION STILL SAYS IT WAITS FOR
   * CAKE AND ITS NEWEST ACTIVATION IS OBSERVED (redesign r4 verify-r4 must_fix 2). The runtime appends the answer before it
   * returns it and writes the resume at the carrying thread's next slot read, so the one read after the answer can still
   * say WAITING_FOR_CAPABILITY. When a read finds the mission has left the wait, the snapshot is read once as well: an end
   * reads it through `refreshMission` (`readAfterMissionEnd`), a resume reads it here. One read at a time, none once the
   * record says otherwise, and after the last one the poll carries on. READS only.
   */
  private rereadMissionAfterActivation(): void {
    const waiting = this.snapshot.mission;
    if (this.activationReread !== null || this.activationRereadsLeft <= 0 || !observedWhileWaiting(waiting) || waiting === null) return;
    this.activationRereadsLeft -= 1;
    const missionId = waiting.mission_id;
    this.activationReread = setTimeout(() => {
      this.activationReread = null;
      void this.refreshMission().then(() => {
        const mission = this.snapshot.mission;
        if (mission !== null && mission.mission_id === missionId && mission.state === 'RUNNING') {
          this.activationRereadsLeft = 0;
          void this.refreshSnapshot();
          return;
        }
        this.rereadMissionAfterActivation();
      });
    }, MISSION_REREAD_AFTER_ACTIVATION_MS);
  }

  /**
   * THE SNAPSHOT READ AGAIN A SECOND LATER, AND EACH SECOND AFTER THAT UNTIL THE NEXT POLL, while the snapshot read after a
   * run's or a mission's end still says that run or that mission holds the slot (redesign r4 verify-r2 must_fix 2,
   * verify-r3 must_fix 1, should_fix honesty S1). The runtime writes a run's end before it lets go of the run
   * (`demo_runtime/supervisor.py`: the run.finished emit, then `_release`), and its mission record can say STOPPED before
   * its snapshot lets go of the mission, so a read sent on the end can still find the slot held; meanwhile the controls
   * say the run or the mission ended and the runtime has not reported itself idle (`panels/commandGuards.runEndedNotIdle`,
   * `missionEndedNotIdle`). One read at a time, at most `SNAPSHOT_REREADS_AFTER_END` per end, and none once the snapshot
   * no longer says so. READS only.
   */
  private rereadSnapshotAfterEnd(): void {
    if (this.snapshotReread !== null || this.snapshotRereadsLeft <= 0 || !this.snapshotHoldsWhatEnded()) return;
    this.snapshotRereadsLeft -= 1;
    this.snapshotReread = setTimeout(() => {
      this.snapshotReread = null;
      void this.refreshSnapshot().then(() => this.rereadSnapshotAfterEnd());
    }, SNAPSHOT_REREAD_AFTER_RUN_END_MS);
  }

  /** Whether the snapshot still names a run whose end the record holds, or says mission_running beside an ended mission record. */
  private snapshotHoldsWhatEnded(): boolean {
    const live = this.liveView;
    if (live.active_run !== null && activeRunOnRecord(live) === null) return true;
    return missionEnded(this.snapshot.mission?.state) && live.capabilities.some((capability) => capability.state !== 'enabled' && capability.reason_code === 'mission_running');
  }

  /**
   * THE SNAPSHOT AND THE CHECKS, READ AGAIN WHEN THE MISSION RECORD SAYS THE MISSION ENDED (redesign r4 verify-r3 must_fix
   * 1): at a Stop's answer and whenever a mission read finds the end of the mission this display held as carried. The
   * capability list, the capability check and the signature check were read while the mission was carried and say
   * `mission_running`; left to the next poll they put "a mission is being carried" beside the card that says it ended.
   * Reads only, sent after the mission read, as `startMission` reads the mission and then the snapshot.
   */
  private async readAfterMissionEnd(): Promise<void> {
    void this.refreshCapabilityCheck();
    void this.refreshSignatureCheck();
    this.snapshotRereadsLeft = SNAPSHOT_REREADS_AFTER_END;
    await this.refreshSnapshot();
    this.rereadSnapshotAfterEnd();
  }

  dispose(): void {
    if (this.missionReread !== null) clearTimeout(this.missionReread);
    this.missionReread = null;
    if (this.snapshotReread !== null) clearTimeout(this.snapshotReread);
    this.snapshotReread = null;
    if (this.activationReread !== null) clearTimeout(this.activationReread);
    this.activationReread = null;
    this.unsubscribeEvents?.();
    this.unsubscribeTelemetry?.();
    if (this.poll !== null) clearInterval(this.poll);
    if (this.telemetryFlush !== null) clearTimeout(this.telemetryFlush);
    this.telemetryFlush = null;
    // The sample that was waiting for the window to close is published now. Dropping it would leave the screen on the
    // one before it, which reads as a feed that stopped a beat earlier than it did.
    this.flushTelemetry();
    this.started = false;
  }

  /**
   * Hold a replaceable telemetry sample until the publication window closes.
   *
   * A sample that arrives while one is already waiting REPLACES it: these are latest-only slots and the superseded
   * sample was never going to be drawn. Nothing is queued and nothing is averaged. The first sample after a quiet
   * period is published on the next turn of the loop rather than held for the whole window, so a feed that starts
   * shows up at once.
   */
  /** The stream's run of arrivals after one more line (a tap sample or a default-camera line) at `at`. */
  private lineSince(at: number): number {
    const pending = this.pendingTelemetry ?? {};
    const telemetry = this.snapshot.telemetry;
    const tap = pending.tap_received_at_ms !== undefined ? pending.tap_received_at_ms : telemetry.tap_received_at_ms;
    const preview = pending.preview_received_at_ms !== undefined ? pending.preview_received_at_ms : telemetry.preview_received_at_ms;
    const last = tap === null && preview === null ? null : Math.max(tap ?? 0, preview ?? 0);
    return arrivalSince(pending.line_since_ms ?? telemetry.line_since_ms ?? null, last, at, TELEMETRY_SILENT_AFTER_MS);
  }

  private queueTelemetry(patch: Partial<TelemetryState>): void {
    this.pendingTelemetry = { ...(this.pendingTelemetry ?? {}), ...patch };
    if (this.telemetryFlush !== null) return;
    const sinceLast = Date.now() - this.telemetryPublishedAtMs;
    const wait = sinceLast >= TELEMETRY_FLUSH_MS ? 0 : TELEMETRY_FLUSH_MS - sinceLast;
    this.telemetryFlush = setTimeout(() => {
      this.telemetryFlush = null;
      this.flushTelemetry();
    }, wait);
  }

  private flushTelemetry(): void {
    const pending = this.pendingTelemetry;
    if (pending === null) return;
    this.pendingTelemetry = null;
    this.telemetryPublishedAtMs = Date.now();
    this.emit({ telemetry: { ...this.snapshot.telemetry, ...pending } });
  }

  /**
   * Read the runtime's snapshot once and fold it in. Public because it is the poll's one unit of work, and a test that
   * wants to show what a second poll does should not have to reach past this class to do it.
   */
  async refreshSnapshot(): Promise<void> {
    // A HANDSHAKE THE RUNTIME HAS NOT ANSWERED IS ASKED AGAIN FIRST, and until it is answered the runtime is not counted
    // as answering: whether this tab may command, and in which contract, is what the handshake says.
    if (this.helloOutstanding) {
      let settled: 'incompatible' | 'unanswered' | 'answered' = 'unanswered';
      try {
        settled = this.applyHello(await this.source.hello());
      } catch {
        /* still not answered */
      }
      if (settled === 'incompatible') {
        this.stopFollowing();
        return;
      }
      if (settled === 'unanswered') {
        this.emit({ sourceError: `the runtime did not answer the handshake: ${this.helloReason ?? 'no reason given'}` });
        return;
      }
    }
    const ask = ++this.snapshotAsks;
    try {
      const snapshot: Snapshot = await this.source.snapshot();
      const lastAt = this.liveView.snapshot_at_ms;
      const failedBefore = this.snapshot.sourceError !== null;
      const sessionBefore = this.liveView.runtime_session;
      this.liveView = applySnapshot(this.liveView, snapshot);
      // ANOTHER RUNTIME SESSION ANSWERS (round 7 item 4): the stream is reopened under it, from this snapshot's cursor, and
      // the run on screen is read back again, so this tab converges to what a fresh tab shows.
      if (sessionBefore !== null && sessionBefore !== snapshot.runtime_session && this.liveView.runtime_session === snapshot.runtime_session) {
        if (this.unsubscribeEvents !== null) this.openEventStream(snapshot.seq_cursor);
        this.selectedRunReadKey = null;
      }
      const at = this.liveView.snapshot_at_ms;
      // WHEN THE RUNTIME HAS ANSWERED SINCE: restarted by a failed exchange or a silence past the current threshold.
      const since = at === null ? null : arrivalSince(failedBefore ? null : (this.snapshot.snapshotSinceMs ?? null), lastAt, at, SNAPSHOT_CURRENT_WITHIN_MS);
      this.emit({ sourceError: null, snapshotSinceMs: since, runtimeAnswered: true });
      this.noteRuntimeSession(snapshot.runtime_session);
      await this.adoptActiveRun(snapshot);
      this.publishView();
      // A READ SENT AFTER A COMMAND'S ANSWER HAS ANSWERED, and the list on the screen now covers whatever that command
      // started (a snapshot refused as late was overtaken by the stream, which then holds the run's start).
      if (this.settleAfterAsk !== null && ask > this.settleAfterAsk) {
        this.settleAfterAsk = null;
        this.noteCommandInFlight();
      }
      // A FINISHED-RUN LIST THAT COULD NOT BE READ is read again once the runtime answers, not left failed until reload.
      if (this.snapshot.history !== null && !this.snapshot.history.ok && !this.historyReading) void this.refreshHistory();
    } catch (error) {
      this.emit({ sourceError: error instanceof Error ? error.message : 'the runtime did not answer' });
    }
  }

  /**
   * Read the Cake resident status once. A READ, and nothing else: it submits nothing, and a page load or a reconnect
   * that calls this reaches no command path.
   *
   * A FAILED READ KEEPS THE LAST GOOD STATUS. Clearing it would turn a runtime that hiccuped into a console that
   * reports no resident, which is the same lie the runtime's own retention exists to prevent, told one layer up.
   */
  async refreshCakeStatus(): Promise<void> {
    try {
      const answer = await this.source.cakeStatus();
      if (answer.ok) this.emit({ cakeStatus: answer.status, cakeStatusError: null });
      else this.emit({ cakeStatusError: answer.reason_code });
    } catch (error) {
      this.emit({ cakeStatusError: error instanceof Error ? error.message : 'the runtime did not answer' });
    }
  }

  /**
   * Read the mission once. A READ: it starts nothing, stops nothing and activates nothing.
   *
   * A FAILED READ KEEPS THE LAST MISSION, and says why beside it. Clearing it would turn one missed poll into a
   * console that reports no mission, which is a different fact from the one that happened.
   *
   * While the mission says it is WAITING, the prepared candidates are read too, once per waiting episode. That read is
   * the only thing the waiting state triggers: no activation is ever submitted by this method or by the poll that
   * calls it.
   */
  async refreshMission(): Promise<boolean> {
    try {
      const answer = await this.source.mission();
      if (!answer.ok) {
        this.emit({ missionError: answer.reason_code, missionErrorDetail: answer.detail ?? null });
        return false;
      }
      const before = this.snapshot.mission;
      const seen = this.snapshot.missionsSeenOpen;
      const seenOpen = !missionEnded(answer.mission.state) && !seen.includes(answer.mission.mission_id) ? [...seen, answer.mission.mission_id] : seen;
      const read = this.snapshot.missionsRead;
      const known = read.some((item) => item.mission_id === answer.mission.mission_id);
      const missionsRead = known ? read.map((item) => (item.mission_id === answer.mission.mission_id ? answer.mission : item)) : [...read, answer.mission];
      this.emit({ mission: answer.mission, missionError: null, missionErrorDetail: null, missionReadAtMs: Date.now(), missionsSeenOpen: seenOpen, missionsRead });
      void this.readMissionRuns();
      // AN END THIS DISPLAY SAW: the mission it held as carried is read as ended. What was read while it was carried is
      // read again now (verify-r3 must_fix 1), not left to the next poll.
      const ended = before !== null && before.mission_id === answer.mission.mission_id && !missionEnded(before.state) && missionEnded(answer.mission.state);
      if (ended) void this.readAfterMissionEnd();
      // ONCE PER MISSION ID, whatever the mission is doing (spec section 3.8): the list is what says what each
      // activation card's candidate was expected to do, after the wait as much as during it.
      if (this.candidatesFor === answer.mission.mission_id) return ended;
      this.candidatesFor = answer.mission.mission_id;
      await this.refreshMissionCandidates();
      return ended;
    } catch (error) {
      this.emit({ missionError: error instanceof Error ? error.message : 'the runtime did not answer' });
      return false;
    }
  }

  /**
   * Read the robot's resident once. A READ: it confirms nothing and grants nothing.
   *
   * A FAILED READ KEEPS THE LAST GOOD ANSWER, and `robotError` says why beside it, for the same reason a failed Cake or
   * mission read does: a runtime that hiccuped did not become a runtime with no robot.
   */
  async refreshRobot(): Promise<void> {
    // OUT-OF-ORDER ANSWERS ARE DROPPED. A poll and a read after a confirmation can overlap; an answer to a read sent
    // BEFORE one that has already been applied would draw an older reading as current, so it is discarded whole.
    this.robotReadsSent += 1;
    const sent = this.robotReadsSent;
    const land = (patch: Partial<StoreSnapshot>): void => {
      if (sent < this.robotReadApplied) return;
      this.robotReadApplied = sent;
      this.emit(patch);
    };
    try {
      const answer = await this.source.robotStatus();
      if (answer.ok) {
        const readAtMs = Date.now();
        land({ robot: answer.status, robotError: null, robotReadAtMs: readAtMs });
      } else land({ robotError: answer.reason_code });
    } catch (error) {
      land({ robotError: error instanceof Error ? error.message : 'the runtime did not answer' });
    }
  }

  /**
   * CONFIRM THE CONNECTED ROBOT, with the session and the host instance the operator was shown.
   *
   * ONCE. A second confirmation while one is in flight is refused here and nothing is queued. The values are the
   * caller's, which is the screen's, and they are sent as given: nothing here re-reads the robot and substitutes a newer
   * session, because a session nobody looked at is not a session anybody confirmed. The runtime compares them with a
   * fresh read of its own and refuses a mismatch; afterwards the robot status and the snapshot are read again, because
   * those, and not this receipt, say whether motion is now permitted.
   */
  async confirmRobotSession(sessionUuid: string, hostInstance: HostInstance): Promise<RobotConfirmOutcome> {
    const press = this.beginPress('robot_confirm');
    if (this.snapshot.confirming) {
      const refused: RobotConfirmOutcome = { kind: 'refused', reason_code: 'a confirmation is already in flight in this tab; nothing was sent a second time' };
      this.settlePress(press, confirmPressReceipt(refused));
      return refused;
    }
    this.emit({ confirming: true });
    try {
      const outcome = await this.source.confirmRobotSession(sessionUuid, hostInstance);
      this.emit({ lastConfirm: outcome });
      this.settlePress(press, confirmPressReceipt(outcome));
      await this.refreshRobot();
      await this.refreshSnapshot();
      return outcome;
    } finally {
      this.emit({ confirming: false });
    }
  }

  /**
   * Read the capability slot check once. A READ: it applies nothing. A failed read keeps the last good state, like every
   * other read here; a source that does not offer the read at all is caught and said, never thrown into the poll.
   */
  async refreshCapabilityCheck(): Promise<void> {
    try {
      const answer = await this.source.capabilityCheck();
      if (answer.ok) {
        this.emit({ capabilityCheck: answer.state, capabilityCheckError: null, capabilityCheckErrorDetail: null, capabilityCheckReadAtMs: Date.now() });
      } else {
        this.emit({ capabilityCheckError: answer.reason_code, capabilityCheckErrorDetail: answer.detail });
      }
    } catch (error) {
      this.emit({ capabilityCheckError: error instanceof Error ? error.message : 'the runtime did not answer', capabilityCheckErrorDetail: null });
    }
  }

  /**
   * APPLY ONE PREPARED PLAN TO THE ROBOT'S CAPABILITY SLOT, with the key, the digest and the session the screen showed.
   *
   * ONCE. A second check while one is in flight from this tab is refused here and nothing is queued. The values are the
   * caller's, which is the screen's, sent as given: nothing here re-reads the robot and substitutes a newer session. The
   * runtime compares them with a fresh read of its own and refuses a mismatch. Afterwards the check's record, the robot
   * and the snapshot are read again, because those, and not this receipt, say what the slot and the host did.
   */
  async applyCapabilityCheck(candidateKey: string, payloadDigest: string, sessionUuid: string): Promise<CapabilityCheckOutcome> {
    const press = this.beginPress('slot_check');
    if (this.snapshot.checking) {
      const refused: CapabilityCheckOutcome = { kind: 'refused', reason_code: 'a check is already in flight in this tab; nothing was sent a second time', detail: null };
      this.settlePress(press, checkPressReceipt(refused, checkOffWords));
      return refused;
    }
    this.emit({ checking: true });
    try {
      const outcome = await this.source.applyCapabilityCheck(candidateKey, payloadDigest, sessionUuid);
      this.emit({ lastCheck: outcome });
      this.settlePress(press, checkPressReceipt(outcome, checkOffWords));
      await this.refreshCapabilityCheck();
      await this.refreshRobot();
      await this.refreshSnapshot();
      return outcome;
    } finally {
      this.emit({ checking: false });
    }
  }

  /**
   * Read the signature policy check once. A READ: it runs nothing. A failed read keeps the last good state, like every
   * other read here; a source that does not offer the read at all is caught and said, never thrown into the poll.
   */
  async refreshSignatureCheck(): Promise<void> {
    try {
      const answer = await this.source.signatureCheck();
      if (answer.ok) {
        this.emit({ signatureCheck: answer.state, signatureCheckError: null, signatureCheckErrorDetail: null, signatureCheckReadAtMs: Date.now() });
      } else {
        this.emit({ signatureCheckError: answer.reason_code, signatureCheckErrorDetail: answer.detail });
      }
    } catch (error) {
      this.emit({ signatureCheckError: error instanceof Error ? error.message : 'the runtime did not answer', signatureCheckErrorDetail: null });
    }
  }

  /**
   * RUN THE RELEASE VERIFIER on the robot over every deployed capsule. NO MOTION, no slot.
   *
   * ONCE. A second run while one is in flight from this tab is refused here and nothing is queued. It carries nothing:
   * the verifier, the store, the keys and the policy are the deployed release's own. Afterwards the check's record and
   * the snapshot are read again, because the record, not this receipt, is the verdict table.
   */
  async runSignatureCheck(): Promise<SignatureCheckOutcome> {
    const press = this.beginPress('signature_check');
    if (this.snapshot.signatureChecking) {
      const refused: SignatureCheckOutcome = { kind: 'refused', reason_code: 'a signature check is already in flight in this tab; nothing was sent a second time', detail: null };
      this.settlePress(press, signaturePressReceipt(refused, signatureOffWords));
      return refused;
    }
    this.emit({ signatureChecking: true });
    try {
      const outcome = await this.source.runSignatureCheck();
      this.emit({ lastSignatureCheck: outcome });
      this.settlePress(press, signaturePressReceipt(outcome, signatureOffWords));
      await this.refreshSignatureCheck();
      await this.refreshSnapshot();
      return outcome;
    } finally {
      this.emit({ signatureChecking: false });
    }
  }

  /** Read the prepared candidate list. A READ; it prepares nothing and activates nothing. */
  async refreshMissionCandidates(): Promise<void> {
    try {
      const answer = await this.source.missionCandidates();
      if (answer.ok) this.emit({ missionCandidates: answer.candidates, missionCandidatesError: null });
      else this.emit({ missionCandidatesError: answer.reason_code });
    } catch (error) {
      this.emit({ missionCandidatesError: error instanceof Error ? error.message : 'the runtime did not answer' });
    }
  }

  /**
   * Ask the runtime to activate ONE prepared candidate, by the key the operator chose from its own list.
   *
   * Three things this deliberately does not do. It does not retry: an activation whose answer was lost is reported as
   * unknown and the runtime's own mission record is read back to find out what became of it. It does not decide
   * anything from the answer: the receipt is kept so the screen can say what came back, and the VERDICT is whatever
   * the mission record says, which is why the mission is re-read here. And it never sends anything but the key.
   */
  async activateCandidate(candidateKey: string): Promise<ActivationOutcome> {
    const press = this.beginPress('candidate');
    if (this.snapshot.activating) {
      const refused: ActivationOutcome = { kind: 'refused', reason_code: 'an activation is already in flight in this tab; nothing was sent a second time' };
      this.settlePress(press, activationPressReceipt(refused));
      return refused;
    }
    this.emit({ activating: true });
    try {
      const outcome = await this.source.activateCandidate(candidateKey);
      this.emit({ lastActivation: outcome });
      this.settlePress(press, activationPressReceipt(outcome), outcome.kind === 'answered' && outcome.operation_uuid !== null ? `activation:${outcome.operation_uuid}` : null);
      // The runtime's record is the authority on what happened, including for a request whose answer was lost.
      await this.refreshMission();
      // AND AGAIN WHILE IT STILL WAITS BESIDE AN OBSERVED ANSWER (verify-r4 must_fix 2): the resume is written after the
      // answer, so the read above can still say WAITING_FOR_CAPABILITY; left to the poll, the card went on waiting for
      // Cake and offered candidates the runtime then refused, for up to 5 s. A refusal leaves the mission waiting, which is
      // what its record says, and reads nothing more.
      if (outcome.kind !== 'refused') {
        this.activationRereadsLeft = MISSION_REREADS_AFTER_ACTIVATION;
        this.rereadMissionAfterActivation();
      }
      // AND THE ROBOT, as the capability check reads it again: the live slot the screen names beside the new card is read
      // after the activation, not left to the next poll (browser r2 N1).
      if (outcome.kind === 'answered') await this.refreshRobot();
      return outcome;
    } finally {
      this.emit({ activating: false });
    }
  }

  /**
   * START THE MISSION, ONCE: the operator-facing Start. It sends POST /api/mission/start and nothing else.
   *
   * ONE REFUSAL IS MADE HERE: a second start while one is in flight from this tab, and nothing is queued. The guards on
   * whether a mission may be started at all (the mission surface, the command guards) are the screen's, applied before
   * the press and again at the click (`design/startPress.ts`). The answer is kept as a receipt and never read as a
   * mission that is running: the mission record and the snapshot are read again, and they are what say whether one
   * started, including for a start whose answer was lost, which is never sent a second time.
   */
  async startMission(confirmed: readonly string[] = []): Promise<MissionStartOutcome> {
    const press = this.beginPress('start');
    // THE START DIALOG'S CONFIRMATIONS are kept in this press, where the dialog says they are; the runtime receives none.
    const withConfirmations = (receipt: Receipt): Receipt =>
      confirmed.length === 0 ? receipt : { ...receipt, text: `Confirmed on this display: ${confirmed.join(' ')} ${receipt.text}` };
    if (this.snapshot.startingMission) {
      const refused: MissionStartOutcome = { kind: 'refused', reason_code: 'a mission start is already in flight in this tab; nothing was sent a second time', detail: null };
      this.settlePress(press, withConfirmations(missionStartReceipt(refused)));
      return refused;
    }
    this.emit({ startingMission: true });
    try {
      const outcome = await this.source.missionStart();
      this.commandAnswered(outcome.kind === 'refused');
      this.emit({ lastMissionStart: outcome });
      this.settlePress(press, withConfirmations(missionStartReceipt(outcome)), outcome.kind === 'started' && outcome.mission_id !== null ? `mission:${outcome.mission_id}:started` : null);
      if (outcome.kind === 'refused') this.noteCommandRefusal(outcome.reason_code);
      await this.refreshMission();
      await this.refreshSnapshot();
      return outcome;
    } finally {
      this.emit({ startingMission: false });
    }
  }

  /** True when the source this page talks to has a mission retry door at all. */
  get retryOffered(): boolean {
    return typeof this.source.missionRetry === 'function';
  }

  /**
   * TRY AGAIN. One request, never queued and never resent: a second press while one is in flight is refused here, and
   * a source with no retry door refuses before anything is sent. The receipt is kept; the mission and the snapshot are
   * read again, and they are what say whether a fresh attempt started. `missionId` is the hold the operator saw; the
   * motion refusals are made before this is called (`design/retryPress.ts`), from the functions Start uses.
   */
  async retryMission(missionId: string): Promise<MissionStartOutcome> {
    const retry = this.source.missionRetry;
    const press = this.beginPress('retry');
    const refuse = (reason: string): MissionStartOutcome => {
      const refused: MissionStartOutcome = { kind: 'refused', reason_code: reason, detail: null };
      this.settlePress(press, missionRetryReceipt(refused));
      return refused;
    };
    if (retry === undefined) return refuse('this runtime does not offer a mission retry');
    if (this.snapshot.retryingMission) return refuse('a retry is already in flight in this tab; nothing was sent a second time');
    this.emit({ retryingMission: true });
    try {
      const outcome = await retry.call(this.source, missionId);
      this.commandAnswered(outcome.kind === 'refused');
      this.emit({ lastMissionRetry: outcome });
      this.settlePress(press, missionRetryReceipt(outcome));
      if (outcome.kind === 'refused') this.noteCommandRefusal(outcome.reason_code);
      await this.refreshMission();
      await this.refreshSnapshot();
      return outcome;
    } finally {
      this.emit({ retryingMission: false });
    }
  }

  /**
   * The runtime that just answered, compared with the one this tab did readiness against.
   *
   * A tab that started before any runtime named a session adopts the first one it hears as the one it booted with;
   * there is nothing else it could have done readiness against. Unless its credential is already superseded: then the
   * console API minted it for itself and serves another now, and the session is followed, never adopted. After that a
   * different session is a REPLACED runtime,
   * and commanding stops here rather than at the first refused POST: the controls go off with the runtime's own word
   * for why, and they stay off, because the way to get a credential and a readiness check for the new runtime is to
   * reload the page and the console must not pretend otherwise.
   */
  private noteRuntimeSession(runtimeSession: string): void {
    const session = this.snapshot.session;
    if (session.booted_runtime_session === null) {
      // A TAB WHOSE CREDENTIAL IS ALREADY SUPERSEDED adopts nothing: the session answering now is not the one its
      // credential was issued for (verify-r1 must_fix 2). It is followed as the current one and nothing more.
      if (session.credential_superseded) {
        if (session.current_runtime_session !== runtimeSession) this.emit({ session: { ...session, current_runtime_session: runtimeSession } });
        return;
      }
      this.emit({ session: { ...session, booted_runtime_session: runtimeSession, current_runtime_session: runtimeSession, booted_answered: true } });
      return;
    }
    const answered = session.booted_answered || runtimeSession === session.booted_runtime_session;
    if (session.current_runtime_session === runtimeSession) {
      if (answered !== session.booted_answered) this.emit({ session: { ...session, booted_answered: answered } });
      return;
    }
    const replaced = runtimeSession !== session.booted_runtime_session;
    this.emit({ session: { ...session, current_runtime_session: runtimeSession, booted_answered: answered, runtime_replaced: replaced || session.runtime_replaced } });
    // The narrower reason is kept: a credential the server refused by name stays the reason the controls are off.
    if (replaced && !session.credential_superseded) this.liveView = markCommandsDisabled(this.liveView, 'runtime_session_changed');
  }

  /**
   * Recover the command that is running RIGHT NOW from the snapshot, for a tab that did not submit it.
   *
   * This is what a reload gets instead of browser storage. The snapshot's active run carries the command id, the run
   * id and the seq the run started at, so a tab that was closed in the middle of a cycle comes back knowing which
   * command is in flight, which run it belongs to and where in the record that run began. Nothing is stored in the
   * browser to make this work, which is what makes it survive a different browser, a different machine and a cleared
   * profile.
   *
   * The track is marked ADOPTED: this tab did not send it and must not say it did. The receipt is then asked for by
   * id, because the runtime's journal is what holds the status and when the id was first admitted, and the snapshot's
   * active run is only what is running.
   */
  private async adoptActiveRun(snapshot: Snapshot): Promise<void> {
    const active = snapshot.active_run;
    if (active === null || active.command_id === null) return;
    const commandId = active.command_id;
    if (this.snapshot.commands.some((track) => track.command_id === commandId)) return;
    const track: CommandTrack = {
      command_id: commandId,
      // The snapshot's own word for the type. A tab that does not know the token still shows what the runtime called it.
      type: (active.command_type ?? 'start_cycle') as Command['type'],
      submitted_at: Date.now(),
      result: { command_id: commandId, status: active.status === 'stopping' ? 'running' : active.status, reason_code: null, run_id: active.run_id },
      unresolved: null,
      adopted: true,
      started_seq: active.started_seq,
      first_seen_wall_utc: null,
    };
    this.emit({ commands: [track, ...this.snapshot.commands].slice(0, 20) });
    // A READ, by the id the snapshot just named. It is never a resubmit and it is awaited so that the adoption is one
    // ordered thing: the track appears with what the snapshot said, and then with what the journal says.
    const receipt = await this.source.resolve(commandId);
    if (receipt === null) return;
    this.emit({
      commands: this.snapshot.commands.map((item) =>
        item.command_id === commandId ? { ...item, result: receipt, first_seen_wall_utc: receipt.first_seen_wall_utc ?? null } : item,
      ),
    });
  }

  /**
   * A refusal that is about THIS TAB's credential rather than about the command.
   *
   * `credential_superseded` is the API saying it served this value itself and has stopped accepting it, which happens
   * when the runtime under it was replaced. It is not a lost response and nothing may be replayed on it: the command
   * never reached the runtime, and the way to command again is a reload that fetches the credential the new runtime
   * owns. The controls go off with that reason on them, and the snapshot is read again so the screen states the runtime
   * it is now looking at.
   */
  private noteCommandRefusal(reasonCode: string | null): void {
    if (reasonCode !== 'credential_superseded' && reasonCode !== 'runtime_session_changed') return;
    this.emit({ session: { ...this.snapshot.session, credential_superseded: reasonCode === 'credential_superseded', runtime_replaced: true } });
    this.liveView = markCommandsDisabled(this.liveView, reasonCode);
    this.publishView();
    void this.refreshSnapshot();
  }

  async refreshHistory(): Promise<void> {
    this.historyReading = true;
    try {
      this.emit({ history: await this.source.history() });
    } finally {
      this.historyReading = false;
    }
    this.followNewestScan();
  }

  /**
   * READ THE NEWEST SCAN'S OWN RECORDS once the runtime lists it (redesign verdict item 8): the Scene draws a scan from
   * its named record (`model/sceneRecords.ts`), never from the live fold's carried events, so a tab opened afresh draws
   * the same Scene as the tab that watched the scan. A READ, once per scan.
   */
  private followNewestScan(): void {
    const history = this.snapshot.history;
    if (history === null || !history.ok) return;
    const scan = newestScanRun(history.entries, this.liveView);
    if (scan === null || this.snapshot.runFolds[scan.run_id] !== undefined) return;
    if (!history.entries.some((entry) => entry.run_id === scan.run_id)) return;
    this.foldRuns([scan.run_id]).catch(() => undefined);
  }

  /**
   * Whether a scan whose end this tab watched is missing from the run list it holds: a list read just as the run ended can
   * predate it, and until the list names the scan its own records are not read back (`followNewestScan`).
   */
  private watchedScanMissingFromHistory(): boolean {
    const history = this.snapshot.history;
    if (history === null || !history.ok) return false;
    const listed = new Set(history.entries.map((entry) => entry.run_id));
    const scan = newestScanRun(history.entries, this.liveView);
    return scan !== null && !listed.has(scan.run_id) && watchedScans(this.liveView).some((watched) => watched.run_id === scan.run_id && watched.finished);
  }

  /** Whether a mission run that is not in progress is missing from the finished-run list this tab holds. */
  private missionRunMissingFromHistory(): boolean {
    const mission = this.snapshot.mission;
    const history = this.snapshot.history;
    if (mission === null || history === null || !history.ok) return false;
    const active = this.snapshot.view.active_run?.run_id ?? null;
    const listed = new Set(history.entries.map((entry) => entry.run_id));
    return mission.targets.flatMap((target) => target.runs).some((runId) => runId !== active && !listed.has(runId) && this.missionRunsRead.has(runId));
  }

  /**
   * THE READ-BACK FOLD OF THE RUN THE SCREEN DESCRIBES, when it pins a decision frame and is what Diagnostics > Evidence
   * draws (`viewForSelectedRun`), or null. A page opened after a run holds that run's records only in its read-back fold,
   * never in the live one, and Evidence draws the frame from it (redesign r4 verify-r4 must_fix 3).
   */
  private readBackFrameView(live: ViewState): ViewState | null {
    const selected = selectedRun(live, this.snapshot.session.runtime_replaced);
    const view = viewForSelectedRun(live, selected, this.snapshot.hydrated);
    return view !== live && view.decision_frame !== null ? view : null;
  }

  /**
   * Fetch the record and the perception for the PINNED frame, and for nothing else: the live or replay fold's, else,
   * when that pins none, the one the selected run's read-back fold pins and Evidence draws (`readBackFrameView`). A frame
   * read back for a finished run adds no scene mark: the marks are the live run's.
   */
  private followDecisionFrame(view: ViewState, readBack: ViewState | null = null): void {
    const own = view.decision_frame?.primary_observation_id ?? null;
    const id = own ?? readBack?.decision_frame?.primary_observation_id ?? null;
    if (id === this.snapshot.frame.observation_id) return;
    const token = ++this.frameToken;
    this.emit({ frame: { observation_id: id, record: null, perception: null, loading: id !== null } });
    if (id === null) return;
    void (async () => {
      const [recordRead, perceptionRead] = await Promise.all([this.source.observationRecord(id), this.source.observationPerception(id)]);
      if (token !== this.frameToken) return;
      const perception = perceptionRead.data;
      this.emit({
        frame: { observation_id: id, record: recordRead.data, perception, loading: false, record_reason: recordRead.reason, perception_reason: perceptionRead.reason },
      });
      if (own !== null) this.addSceneMark(id, perception, view);
    })();
  }

  /** Keep a pad-frame position only when THIS frame's pad frame resolved one. */
  private addSceneMark(observationId: string, perception: PerceptionDto | null, view: ViewState): void {
    const centre = perception?.cube_by_template?.cube_center_pad_m ?? null;
    if (centre === null || perception?.pad_by_template?.pad_frame_valid !== true) return;
    if (this.snapshot.sceneMarks.some((mark) => mark.observation_id === observationId)) return;
    const mark: SceneMark = {
      observation_id: observationId,
      state: view.machine?.state ?? null,
      point_mm: [centre[0] * 1000, centre[1] * 1000],
      seq: view.decision_frame?.pinned_at_seq ?? view.seq_cursor,
    };
    this.emit({ sceneMarks: [...this.snapshot.sceneMarks, mark].slice(-24) });
  }

  get currentSource(): ConsoleSource {
    return this.source;
  }

  /** Publish `commandInFlight` when it changes: a command answer outstanding, or one awaiting its snapshot read. */
  private noteCommandInFlight(): void {
    const inFlight = this.submitsInFlight > 0 || this.settleAfterAsk !== null;
    if (inFlight !== this.snapshot.commandInFlight) this.emit({ commandInFlight: inFlight });
  }

  /**
   * A COMMAND'S ANSWER IS IN. One that refused started nothing. Any other (accepted, or lost, which may have been
   * accepted) may have started a run the list on the screen does not show, and waits for a snapshot read sent after now.
   */
  private commandAnswered(refused: boolean): void {
    if (!refused) this.settleAfterAsk = this.snapshotAsks;
    this.noteCommandInFlight();
  }

  /**
   * Submit ONE command. The id is minted here, before anything is sent, so a lost response can be resolved rather
   * than guessed at. Nothing is ever queued and nothing is ever sent twice.
   */
  async submit(command: Command): Promise<CommandTrack> {
    const control: PressControl | null =
      command.type === 'return_arm' ? 'return_arm' : command.type === 'observe' ? 'observe' : (command.type as string) === 'scan_scene' ? 'scan_scene' : command.type === 'start_cycle' ? 'start' : null;
    const press = control === null ? null : this.beginPress(control);
    const commandId = mintCommandId();
    const track: CommandTrack = { command_id: commandId, type: command.type, submitted_at: Date.now(), result: null, unresolved: null };
    this.submitsInFlight += 1;
    this.emit({ commands: [track, ...this.snapshot.commands].slice(0, 20), commandInFlight: true });
    let outcome: SubmitOutcome | null = null;
    try {
      outcome = await this.source.submit(commandId, command);
    } finally {
      this.submitsInFlight -= 1;
      this.commandAnswered(outcome !== null && outcome.kind === 'result' && outcome.result.status === 'rejected');
    }
    const settled: CommandTrack =
      outcome.kind === 'result'
        ? { ...track, result: outcome.result, first_seen_wall_utc: outcome.result.first_seen_wall_utc ?? null }
        : { ...track, unresolved: outcome.detail };
    this.emit({ commands: this.snapshot.commands.map((item) => (item.command_id === commandId ? settled : item)) });
    if (press !== null) {
      const runId = settled.result?.run_id ?? null;
      this.settlePress(press, commandPressReceipt(settled.result, settled.unresolved), settled.result?.status !== 'rejected' && runId !== null ? `run:${runId}` : null);
    }
    if (outcome.kind === 'result') this.noteCommandRefusal(outcome.result.reason_code);
    // THE RUN IS ON THE RUNTIME'S LIST THE MOMENT IT ANSWERS: the snapshot is read again now, as startMission does, and not
    // left to the next poll. A refusal started nothing and asks for nothing.
    if (this.settleAfterAsk !== null) await this.refreshSnapshot();
    return settled;
  }

  /** Ask the runtime again what became of a command. This is the only way a lost response is ever resolved. */
  async resolveCommand(commandId: string): Promise<void> {
    const result = await this.source.resolve(commandId);
    if (result === null) return;
    this.emit({
      commands: this.snapshot.commands.map((item) => (item.command_id === commandId ? { ...item, result, unresolved: null } : item)),
    });
  }

  /**
   * State a finding about the command whose execution is not known, and let the RUNTIME reconcile it.
   *
   * Three things this deliberately does not do. It does not resend the command: nothing in this method holds one. It
   * does not clear `view.unresolved`, ever: that field is snapshot-derived, and the block lifting is the runtime's
   * finding and not this tab's. And it does not decide anything from the answer; it keeps the answer so the screen can
   * show what the runtime found, and then re-reads the snapshot, which is what says whether commands are allowed again.
   */
  async resolveUncertain(commandId: string, finding: string, note: string | null): Promise<ResolutionOutcome> {
    if (this.snapshot.resolving) {
      return { kind: 'unknown', detail: 'a resolve is already in flight for this tab; nothing was sent a second time' };
    }
    this.emit({ resolving: true });
    try {
      const outcome = await this.source.resolveUncertain(commandId, finding, note);
      this.emit({ lastResolution: outcome });
      // The snapshot is the authority on whether the block is gone. Asking again here is what makes the controls come
      // back, and it is also what keeps them off when the runtime decided the statement was not enough.
      await this.refreshSnapshot();
      return outcome;
    } finally {
      this.emit({ resolving: false });
    }
  }

  /**
   * Ask the controller to stop, ONCE, and keep the id it was asked under.
   *
   * WHAT IT STOPS. Nothing here names a run. The stop goes to the runtime, which applies it to the run that is active
   * on the robot, and that stays true while a RECORDING is open on the screen: a replay is a fold of records this tab
   * is looking at and it is not what the robot is doing. Passing a run id from the replay would be the one way to aim
   * this at the wrong thing, so no run id is passed at all.
   *
   * WHAT IS KEPT. The ack carries the command id the source minted, and it is tracked like every other command, so a
   * stop whose answer never arrived can be asked about by that id instead of the button being pressed a second time.
   * A refusal is a refusal and a request whose fate is unknown says so. Nothing is retried here.
   */
  async requestStop(): Promise<StopAck> {
    const press = this.beginPress('stop');
    const ack = await this.source.stop();
    const missionId = ack.mission?.mission_id ?? null;
    this.settlePress(press, stopReceipt(ack), ack.outcome !== 'taken' ? null : missionId !== null && ack.mission?.stopping === true ? `mission:${missionId}:stop` : null);
    const track: CommandTrack = {
      command_id: ack.command_id,
      type: 'stop',
      submitted_at: Date.now(),
      result: ack.result,
      unresolved: ack.outcome === 'unknown' ? ack.reason : null,
      first_seen_wall_utc: ack.result?.first_seen_wall_utc ?? null,
    };
    this.emit({ lastStop: ack, commands: [track, ...this.snapshot.commands.filter((item) => item.command_id !== ack.command_id)].slice(0, 20) });
    // A TAKEOVER (verify-r4 must_fix 4) says the server refused THIS tab's credential as superseded, whatever the second
    // Stop's answer: that is what this tab holds, and every other control it offers is said so, as after any such refusal.
    if (ack.takeover != null) this.noteCommandRefusal('credential_superseded');
    else if (ack.outcome === 'refused') this.noteCommandRefusal(ack.reason);
    // THE MISSION IS READ BACK, NOT TAKEN FROM THE ANSWER. The stop's mission part says a stop was asked for; the record
    // says what the mission came to. A READ, after the stop has already been answered, so it never delays one.
    // THEN THE SNAPSHOT, as startMission reads it after the mission (verify-r3 must_fix 1): a stop at a hold ends no run, so
    // no run.finished reads it, and the list read while the mission was carried said "a mission is being carried" beside
    // the card that says it stopped for up to one poll. A mission read that saw the end has read it already.
    void (async () => {
      const ended = ack.mission === null ? false : await this.refreshMission();
      if (!ended) await this.readAfterMissionEnd();
    })();
    return ack;
  }

  /**
   * Open a recorded run. A replay is opened only on records that actually arrived: folding an empty or unreadable
   * answer would draw the initial state of the reducer under a badge that says REPLAY, which reads as a run in which
   * nothing happened.
   */
  async openReplay(runId: string): Promise<void> {
    const answer = await this.source.historyEvents(runId);
    if (!answer.ok) {
      this.emit({ replayError: `the records of ${runId} could not be read: ${answer.reason_code}` });
      return;
    }
    this.emit({
      replay: { run_id: answer.run_id, events: answer.events, cursor: answer.events.length, origins: answer.origins },
      replayError: null,
    });
    this.publishView();
  }

  /**
   * Read one run's controller output. The run id is explicit: nothing here reads "the run on the screen", so a log
   * shown under a run id is always the log of THAT run.
   */
  async openRunOutput(runId: string, stream: 'stdout' | 'stderr' = 'stdout'): Promise<void> {
    this.emit({ runOutputLoading: true, runOutputError: null });
    this.publishView();
    const answer = await this.source.runOutput(runId, stream);
    if (!answer.ok) {
      this.emit({ runOutput: null, runOutputError: answer.reason_code, runOutputLoading: false });
      this.publishView();
      return;
    }
    this.emit({
      runOutput: { run_id: answer.run_id, stream: answer.stream, text: answer.text, total_bytes: answer.total_bytes, truncated: answer.truncated },
      runOutputError: null,
      runOutputLoading: false,
    });
    this.publishView();
  }

  /**
   * Read one run's retained records and fold them in isolation.
   *
   * STALE ANSWERS CANNOT REPLACE A NEWER SELECTION. Each call takes a token; when the answer arrives it is applied
   * only if the token is still the current one. Two selections in flight therefore end with the newer one's records
   * on the screen, whichever answered first. A run already hydrated is not fetched again: a finished run's records
   * do not change.
   */
  async hydrateRun(runId: string): Promise<void> {
    if (this.snapshot.hydrated?.run_id === runId || this.snapshot.hydratingRunId === runId) return;
    const token = ++this.hydrateToken;
    this.emit({ hydratingRunId: runId, hydratedError: null });
    this.publishView();
    const answer = await this.source.historyEvents(runId);
    if (token !== this.hydrateToken) return;  // a newer selection was asked for while this was in flight
    if (!answer.ok) {
      this.emit({ hydrated: null, hydratedError: answer.reason_code, hydratingRunId: null });
      this.publishView();
      return;
    }
    const folded = foldEvents(initialState(), answer.events);
    this.emit({
      hydrated: { run_id: answer.run_id, view: folded },
      runFolds: this.withRunFold(answer.run_id, folded),
      hydratedError: null,
      hydratingRunId: null,
    });
    this.publishView();
  }

  /** The cache with one more fold, the oldest dropped past the bound. Insertion order is the age. */
  private withRunFold(runId: string, view: ViewState): Record<string, ViewState> {
    const next: Record<string, ViewState> = {};
    const kept = Object.entries(this.snapshot.runFolds).filter(([id]) => id !== runId);
    for (const [id, held] of kept.slice(Math.max(0, kept.length - (RUN_FOLDS_MAX - 1)))) next[id] = held;
    next[runId] = view;
    return next;
  }

  /**
   * FOLD THE NAMED RUNS this page does not hold yet, one read each through the history path, in order. A run already
   * folded or being read is not asked for again; one the runtime could not serve is left out and not recorded, and so is
   * one whose records do not reach its end yet (the runtime serves a run in progress too), so it is read again later
   * rather than kept as though it had ended there. It fetches; it commands nothing.
   */
  async foldRuns(runIds: readonly string[]): Promise<void> {
    for (const runId of runIds) {
      if (this.snapshot.runFolds[runId] !== undefined || this.foldingRuns.has(runId)) continue;
      this.foldingRuns.add(runId);
      try {
        const answer = await this.source.historyEvents(runId);
        if (!answer.ok || !answer.events.some((event) => event.kind === 'run.finished')) continue;
        this.emit({ runFolds: this.withRunFold(answer.run_id, foldEvents(initialState(), answer.events)) });
      } finally {
        this.foldingRuns.delete(runId);
      }
    }
  }

  setReplayCursor(cursor: number): void {
    const replay = this.snapshot.replay;
    if (replay === null) return;
    this.emit({ replay: { ...replay, cursor: Math.max(0, Math.min(cursor, replay.events.length)) } });
    this.publishView();
  }

  closeReplay(): void {
    this.emit({ replay: null, replayError: null });
    this.publishView();
  }
}
