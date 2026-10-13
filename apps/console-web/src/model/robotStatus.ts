// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ROBOT'S OWN CAKE RESIDENT, AS THE RUNTIME REACHES IT: GET /api/robot.
//
// This is the surface that answers the operator's first two questions: which LeKiwi this console is connected to, and
// whether its Cake resident and its supervised host are there. It is versioned on its own (`robot_status_version`),
// apart from contract v1 and from the `/api/cake` status, and this file reads it the way every runtime surface is read
// here: field by field, with an absent field read as "the runtime did not say" and never as a zero, a false or an empty
// inventory.
//
// FOUR RULES DECIDE WHAT THE SCREEN MAY SAY FROM IT.
//
//   A RESIDENT ON THIS COMPUTER IS NEVER THE ROBOT'S. The runtime reports `configured: false` for one, and this file
//   draws nothing about a robot from such an answer. The `/api/cake` surface does not say which machine answered, so
//   it is never used to say anything about the robot either.
//
//   A RETAINED READING SAYS SO, WITH ITS AGE. `stale` is the runtime marking a section as kept from an earlier read; a
//   read of this surface that FAILED keeps the last good answer and marks it stale too. Neither is ever replaced by an
//   empty robot, and neither is ever drawn as current.
//
//   MOTION PERMISSION IS THE RUNTIME'S. `permitted` true is the only permitted; this console explains a hold in the
//   runtime's own reason code and sentence and never lifts one. The confirm action is offered only while the runtime
//   reports a host instance RUNNING under the supervisor slot and identified (`confirmable`), and it posts the CURRENT
//   session and host instance (slot instance handle, spawn sequence, pid) exactly as the screen displays them; the
//   runtime compares each with a fresh read of its own.
//
//   A COMPONENT RECOVERY IS NOT A RESIDENT RESTART. The supervisor restarting the host or a capability process inside
//   one resident session is counted in `health` (`restarts`); a resident restart is a different session. The two are
//   stated apart. BOTH A RESIDENT RESTART AND A HOST RESTART REVOKE motion permission: the first changes the session,
//   the second the host instance (its spawn sequence, whatever the pid). A capability process restart revokes nothing.

import { ROBOT_ANSWER_STALE_AFTER_MS } from './robot';

/** The only robot status version this console speaks. Separate from the contract version and from cake_status. */
export const ROBOT_STATUS_VERSION = '2';

export interface RobotObserved {
  /** For an operator to read. Ages are never computed from it. */
  wall_utc: string | null;
  /** Milliseconds on the runtime's laptop_monotonic clock, measured when the answer was assembled. */
  age_ms: number | null;
}

export interface RobotForward {
  state: string;
  reason: string | null;
  since_wall_utc: string | null;
  since_age_ms: number | null;
  starts: number | null;
  deaths: number | null;
  restarts: number | null;
  next_attempt_in_ms: number | null;
}

export interface RobotResident {
  reachable: boolean;
  stale: boolean;
  reason_code: string | null;
  session_uuid: string | null;
  build_identity: string | null;
  plan_digest: string | null;
  target_profile_digest: string | null;
  epoch: number | null;
  /** Sticky within the runtime; null before two answers could be compared. */
  session_changed: boolean | null;
  observed: RobotObserved | null;
}

export interface RobotSlotRow {
  name: string;
  uuid: string | null;
  capsule: string | null;
  generation: number | null;
  /** The slot's instance handle: a u64, carried as a decimal STRING because a number cannot hold it exactly. */
  instance: string | null;
  state: string;
}

export interface RobotHealthRow {
  slot: string | null;
  state_name: string;
  /** A pid ON THE ROBOT. */
  child_pid: number | null;
  /** How many times the supervisor restarted this child inside the current resident session. */
  restarts: number | null;
  spawns: number | null;
  last_status: number | null;
}

export interface RobotSection<T> {
  stale: boolean;
  reason_code: string | null;
  items: T[];
  observed: RobotObserved | null;
}

export interface RobotHost {
  supervised: boolean;
  reason_code: string | null;
  slot_uuid: string | null;
  slot_state: string | null;
  /** The supervisor slot's instance handle, as a decimal string. */
  slot_instance: string | null;
  /** The supervised host's pid ON THE ROBOT, as get-health reported it. */
  child_pid: number | null;
  /** How many hosts the supervisor has spawned: a host restart moves it even when the pid is reused. */
  spawn_sequence: number | null;
  health_state: string | null;
  readiness_state: string | null;
  readiness_age_ms: number | null;
  observed: RobotObserved | null;
}

export interface RobotCapability {
  configured: boolean;
  name: string | null;
  active: boolean;
  reason_code: string | null;
  capsule: string | null;
  generation: number | null;
  child_pid: number | null;
  stale: boolean;
  observed: RobotObserved | null;
}

export interface RobotUpdateRow {
  sequence: number | null;
  event: string;
  name: string;
  kind: string;
  arg0: number | null;
  arg1: number | null;
  epoch: number | null;
}

/**
 * WHICH host process the robot's supervisor slot runs, as a confirmation names it. The slot instance handle changes
 * when the slot is re-created, the spawn sequence when the host restarts (even if the pid is reused), the pid with the
 * process. Together with the resident session, it is what motion permission is bound to.
 */
export interface HostInstance {
  slot_instance: string;
  spawn_sequence: number;
  pid: number;
}

export type ChangedComponent = 'session' | 'slot_instance' | 'spawn_sequence' | 'pid' | 'host_stopped' | 'identity';

export interface RobotMotionPermission {
  applies: boolean;
  permitted: boolean | null;
  reason_code: string | null;
  detail: string | null;
  /** True only when the runtime reports a current session and a RUNNING, identified host instance to confirm. */
  confirmable: boolean;
  /** Which component's change revoked the last confirmation, while it stays revoked. */
  changed_component: ChangedComponent | null;
  confirmed_session_uuid: string | null;
  confirmed_host_instance: HostInstance | null;
  confirmed_wall_utc: string | null;
  current_session_uuid: string | null;
  /** The host instance the runtime reports as current; null for no running or no identified host. */
  current_host_instance: HostInstance | null;
  /** Why there is no current host instance to confirm, in the runtime's code. */
  current_host_reason_code: string | null;
  observed: RobotObserved | null;
}

/**
 * WHICH ROBOT, and whether the resident answering reports the BUILD pinned for the robot. The build identity identifies
 * the SOFTWARE the resident runs, and the pin refuses a different build and nothing more; which deployment is observed
 * is established by the authenticated connection to the intended robot (the ssh forward to that robot's admin socket,
 * or the local socket on the robot) and the session that endpoint's resident reports now. `is_robot` (the runtime's kept
 * name) false means a resident answered that does not report the pinned build, and then nothing else in the answer
 * describes it as the robot. The answering resident's target profile is information only: it refuses nothing.
 * `display_label` is the operator's own public name for the robot; the robot id is never sent.
 */
export interface RobotIdentity {
  is_robot: boolean | null;
  reason_code: string | null;
  expected_build_identity: string | null;
  answering_build_identity: string | null;
  answering_target_profile_digest: string | null;
  display_label: string | null;
  labelled: boolean;
  build_identity: string | null;
  session_uuid: string | null;
  stale: boolean;
  observed: RobotObserved | null;
}

export interface RobotStatus {
  robot_status_version: string;
  configured: boolean;
  /** robot_resident, this_laptop or not_configured. A token this console does not know is kept as itself. */
  deployment: string;
  reachable: boolean;
  reason_code: string | null;
  stale: boolean;
  clock_domain: string | null;
  /** The robot's public display label, when the runtime reports one. The robot id itself is never sent. */
  display_label: string | null;
  identity: RobotIdentity;
  connection: {
    deployment: string | null;
    forward: RobotForward | null;
    observed: RobotObserved | null;
  };
  resident: RobotResident;
  slots: RobotSection<RobotSlotRow>;
  health: RobotSection<RobotHealthRow>;
  host: RobotHost;
  capability: RobotCapability;
  updates: RobotSection<RobotUpdateRow> & { dropped: number | null; excluded_admin_records: number | null; other_records: number | null };
  motion_permission: RobotMotionPermission;
}

/* ---- reading it -------------------------------------------------------------------------------------------------- */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function flag(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function rec(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

const SLOT_INSTANCE = /^[1-9][0-9]{0,19}$/;
const MAX_HOST_PID = 4_194_304;

/** A slot instance handle as the runtime sent it: a canonical decimal string, or nothing. Never a number. */
function slotInstance(value: unknown): string | null {
  return typeof value === 'string' && SLOT_INSTANCE.test(value) ? value : null;
}

function wholeIn(value: unknown, low: number, high: number): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high ? value : null;
}

/** A host instance, read whole or not at all: a partial instance is not one anybody can confirm. */
export function hostInstance(value: unknown): HostInstance | null {
  if (!isRecord(value)) return null;
  const slot = slotInstance(value['slot_instance']);
  const spawns = wholeIn(value['spawn_sequence'], 1, Number.MAX_SAFE_INTEGER);
  const pid = wholeIn(value['pid'], 1, MAX_HOST_PID);
  if (slot === null || spawns === null || pid === null) return null;
  return { slot_instance: slot, spawn_sequence: spawns, pid };
}

const CHANGED: readonly ChangedComponent[] = ['session', 'slot_instance', 'spawn_sequence', 'pid', 'host_stopped', 'identity'];

function changedComponent(value: unknown): ChangedComponent | null {
  return CHANGED.find((item) => item === value) ?? null;
}

function observed(value: unknown): RobotObserved | null {
  if (!isRecord(value)) return null;
  return { wall_utc: text(value['wall_utc']), age_ms: num(value['age_ms']) };
}

function rows<T>(value: unknown, read: (row: Record<string, unknown>) => T | null): T[] {
  if (!Array.isArray(value)) return [];
  const out: T[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const row = read(item);
    if (row !== null) out.push(row);
  }
  return out;
}

function section<T>(value: unknown, read: (row: Record<string, unknown>) => T | null): RobotSection<T> {
  const body = rec(value);
  return {
    // A section that does not say it is current is not read as current.
    stale: flag(body['stale']) ?? false,
    reason_code: text(body['reason_code']),
    items: rows(body['items'], read),
    observed: observed(body['observed']),
  };
}

function forward(value: unknown): RobotForward | null {
  if (!isRecord(value)) return null;
  const state = text(value['state']);
  if (state === null) return null;
  return {
    state,
    reason: text(value['reason']),
    since_wall_utc: text(value['since_wall_utc']),
    since_age_ms: num(value['since_age_ms']),
    starts: num(value['starts']),
    deaths: num(value['deaths']),
    restarts: num(value['restarts']),
    next_attempt_in_ms: num(value['next_attempt_in_ms']),
  };
}

/**
 * The robot status, or null when the answer is not one this console speaks.
 *
 * The version is checked before anything is read out of it, which is the rule the contract handshake and the Cake
 * status already follow: a surface this console does not speak is refused, not guessed at.
 */
export function parseRobotStatus(value: unknown): RobotStatus | null {
  if (!isRecord(value) || value['robot_status_version'] !== ROBOT_STATUS_VERSION) return null;
  const connection = rec(value['connection']);
  const resident = rec(value['resident']);
  const host = rec(value['host']);
  const capability = rec(value['capability']);
  const updates = rec(value['updates']);
  const motion = rec(value['motion_permission']);
  const identity = rec(value['identity']);
  const configured = flag(value['configured']) ?? false;
  return {
    robot_status_version: ROBOT_STATUS_VERSION,
    configured,
    deployment: text(value['deployment']) ?? 'not_configured',
    // Only an explicit true is an answer.
    reachable: flag(value['reachable']) === true,
    reason_code: text(value['reason_code']),
    stale: flag(value['stale']) ?? false,
    clock_domain: text(value['clock_domain']),
    // The identity section is the ONE place the label is read from; nothing outside it is taken as the robot's name.
    display_label: text(identity['display_label']),
    identity: {
      is_robot: flag(identity['is_robot']),
      reason_code: text(identity['reason_code']),
      expected_build_identity: text(identity['expected_build_identity']),
      answering_build_identity: text(identity['answering_build_identity']),
      answering_target_profile_digest: text(identity['answering_target_profile_digest']),
      display_label: text(identity['display_label']),
      labelled: flag(identity['labelled']) === true,
      build_identity: text(identity['build_identity']),
      session_uuid: text(identity['session_uuid']),
      stale: flag(identity['stale']) ?? false,
      observed: observed(identity['observed']),
    },
    connection: {
      deployment: text(connection['deployment']),
      forward: forward(connection['forward']),
      observed: observed(connection['observed']),
    },
    resident: {
      reachable: flag(resident['reachable']) === true,
      stale: flag(resident['stale']) ?? false,
      reason_code: text(resident['reason_code']),
      session_uuid: text(resident['session_uuid']),
      build_identity: text(resident['build_identity']),
      plan_digest: text(resident['plan_digest']),
      target_profile_digest: text(resident['target_profile_digest']),
      epoch: num(resident['epoch']),
      session_changed: flag(resident['session_changed']),
      observed: observed(resident['observed']),
    },
    slots: section(value['slots'], (row) => {
      const name = text(row['name']);
      const state = text(row['state']);
      if (name === null || state === null) return null;
      return {
        name,
        uuid: text(row['uuid']),
        capsule: text(row['capsule']),
        generation: num(row['generation']),
        instance: slotInstance(row['instance']),
        state,
      };
    }),
    health: section(value['health'], (row) => {
      const state = text(row['state_name']);
      if (state === null) return null;
      return {
        slot: text(row['slot']),
        state_name: state,
        child_pid: num(row['child_pid']),
        restarts: num(row['restarts']),
        spawns: num(row['spawns']),
        last_status: num(row['last_status']),
      };
    }),
    host: {
      // Never true from anything but an explicit true.
      supervised: flag(host['supervised']) === true,
      reason_code: text(host['reason_code']),
      slot_uuid: text(host['slot_uuid']),
      slot_state: text(host['slot_state']),
      slot_instance: slotInstance(host['slot_instance']),
      child_pid: num(host['child_pid']),
      spawn_sequence: num(host['spawn_sequence']),
      health_state: text(host['health_state']),
      readiness_state: text(host['readiness_state']),
      readiness_age_ms: num(host['readiness_age_ms']),
      observed: observed(host['observed']),
    },
    capability: {
      configured: flag(capability['configured']) === true,
      name: text(capability['name']),
      active: flag(capability['active']) === true,
      reason_code: text(capability['reason_code']),
      capsule: text(capability['capsule']),
      generation: num(capability['generation']),
      child_pid: num(capability['child_pid']),
      stale: flag(capability['stale']) ?? false,
      observed: observed(capability['observed']),
    },
    updates: {
      ...section(value['updates'], (row) => {
        const event = text(row['event']);
        const name = text(row['name']);
        if (event === null || name === null) return null;
        return {
          sequence: num(row['sequence']),
          event,
          name,
          kind: text(row['kind']) ?? 'unstated',
          arg0: num(row['arg0']),
          arg1: num(row['arg1']),
          epoch: num(row['epoch']),
        };
      }),
      dropped: num(updates['dropped']),
      excluded_admin_records: num(updates['excluded_admin_records']),
      other_records: num(updates['other_records']),
    },
    motion_permission: {
      applies: flag(motion['applies']) === true,
      permitted: flag(motion['permitted']),
      reason_code: text(motion['reason_code']),
      detail: text(motion['detail']),
      // Only an explicit true offers anything to confirm.
      confirmable: flag(motion['confirmable']) === true,
      changed_component: changedComponent(motion['changed_component']),
      confirmed_session_uuid: text(motion['confirmed_session_uuid']),
      confirmed_host_instance: hostInstance(motion['confirmed_host_instance']),
      confirmed_wall_utc: text(motion['confirmed_wall_utc']),
      current_session_uuid: text(motion['current_session_uuid']),
      current_host_instance: hostInstance(motion['current_host_instance']),
      current_host_reason_code: text(motion['current_host_reason_code']),
      observed: observed(motion['observed']),
    },
  };
}

/* ---- what the screen says ------------------------------------------------------------------------------------- */

/** What the store holds about this surface: the last good answer, when it arrived here, and the last failure. */
export interface RobotReadState {
  status: RobotStatus | null;
  error: string | null;
  /** When the last GOOD answer arrived, on this computer's clock. */
  readAtMs: number | null;
}

export type RobotLinkState = 'answering' | 'not_answering' | 'not_the_robot' | 'not_configured' | 'laptop_only' | 'not_read';

export interface RobotLink {
  /** The robot's display label, or the sentence for its absence. It never falls back to a runtime session id. */
  label: string;
  labelKnown: boolean;
  state: RobotLinkState;
  /** A few words for the header. */
  headline: string;
  /** One sentence of why. */
  detail: string;
  /** True when what is drawn about the robot is retained from an earlier reading. */
  stale: boolean;
  /**
   * How old the retained resident reading was when the runtime last answered, on the RUNTIME's clock. Null when there
   * is no reading. It is never added to `sinceReadMs`: the two are measured on different clocks.
   */
  residentAgeMs: number | null;
  /** How long ago this tab last got a good answer from GET /api/robot, on this computer's clock. */
  sinceReadMs: number | null;
}

export const ROBOT_NAME_NOT_REPORTED = 'Robot name not reported';

export function robotLink(read: RobotReadState, nowMs: number): RobotLink {
  const status = read.status;
  const sinceReadMs = read.readAtMs === null ? null : Math.max(0, nowMs - read.readAtMs);
  if (status === null) {
    return {
      label: ROBOT_NAME_NOT_REPORTED,
      labelKnown: false,
      state: 'not_read',
      headline: 'Robot status not read',
      detail: read.error ?? 'this runtime has not answered about a robot yet',
      stale: false,
      residentAgeMs: null,
      sinceReadMs,
    };
  }
  const label = status.display_label ?? ROBOT_NAME_NOT_REPORTED;
  const labelKnown = status.display_label !== null;
  if (!status.configured) {
    // A RESIDENT ON THIS COMPUTER IS NEVER THE ROBOT'S. The runtime says which of the two it has; neither is a robot.
    const laptop = status.deployment === 'this_laptop';
    return {
      label,
      labelKnown,
      state: laptop ? 'laptop_only' : 'not_configured',
      headline: 'No robot configured',
      detail: laptop
        ? 'this runtime reads a resident on this computer, which is never reported as the robot\'s'
        : 'this runtime was not configured with a robot, so nothing about one was asked',
      stale: false,
      residentAgeMs: null,
      sinceReadMs,
    };
  }
  // A RESIDENT THAT DOES NOT REPORT THE PINNED BUILD IS NOT SHOWN AS THE ROBOT. The runtime then takes nothing from its
  // answer; what is shown is the last reading that did report it, which is retained by definition. The state keeps its
  // name; the words say what the pin establishes: which software, not which machine.
  if (status.identity.is_robot === false) {
    return {
      label,
      labelKnown,
      state: 'not_the_robot',
      headline: 'Not the pinned build',
      detail: `the resident answering through the robot connection reports a build other than the one pinned for the robot (${status.identity.reason_code ?? 'no reason was given'}); nothing it said is shown as the robot's`,
      stale: true,
      residentAgeMs: status.resident.observed?.age_ms ?? null,
      sinceReadMs,
    };
  }
  // THE READ ITSELF FAILING, or going quiet, makes everything held from it retained, whatever it said when it came.
  const readStale = read.error !== null || (sinceReadMs !== null && sinceReadMs > ROBOT_ANSWER_STALE_AFTER_MS);
  const residentAgeMs = status.resident.observed?.age_ms ?? null;
  if (status.reachable && !status.stale && !readStale) {
    return {
      label,
      labelKnown,
      state: 'answering',
      headline: 'Robot resident answering',
      detail: `the robot's Cake resident answered the most recent read${forwardWords(status.connection.forward)}`,
      stale: false,
      residentAgeMs,
      sinceReadMs,
    };
  }
  const why = read.error !== null
    ? `this runtime did not answer the last read (${read.error})`
    : readStale
      ? 'no answer about the robot has reached this tab for a while'
      : `the robot's resident did not answer (${status.reason_code ?? 'no reason was given'})`;
  return {
    label,
    labelKnown,
    state: 'not_answering',
    headline: 'Robot not answering',
    detail: `${why}${forwardWords(status.connection.forward)}; what is shown about it is its last answer`,
    stale: true,
    residentAgeMs,
    sinceReadMs,
  };
}

function forwardWords(forward: RobotForward | null): string {
  if (forward === null) return '';
  return `, connection ${forward.state}${forward.reason === null ? '' : ` (${forward.reason})`}`;
}

/* ---- motion permission ------------------------------------------------------------------------------------------ */

export type HoldCause =
  | 'never_confirmed'
  | 'resident_restarted'
  | 'host_restarted'
  | 'host_unreadable'
  | 'identity_changed'
  | 'not_the_robot'
  | 'unreachable'
  | 'other';

/** The cause of a hold, from the runtime's own reason code. A code this console has no word for is `other`. */
export function holdCause(code: string | null): HoldCause {
  const c = code ?? '';
  // The runtime's own codes first (demo_runtime/robot_link.py), then the families they belong to.
  if (c === 'robot_host_unreadable') return 'host_unreadable';
  if (c === 'robot_identity_changed') return 'identity_changed';
  if (c === 'robot_build_identity_unreadable') return 'not_the_robot';
  if (c === 'robot_session_unreadable' || c === 'robot_resident_not_answering' || c === 'robot_forward_socket_stale') return 'unreachable';
  if (c.includes('not_confirmed') || c.includes('never_confirmed') || c.includes('unconfirmed')) return 'never_confirmed';
  if (c.includes('session_changed') || c.includes('resident_restart')) return 'resident_restarted';
  if (c.includes('host')) return 'host_restarted';
  if (c.includes('not_the_robot') || c.includes('not_robot') || c.includes('laptop') || c.includes('not_configured')) return 'not_the_robot';
  if (c.includes('unreachable') || c.includes('not_answering')) return 'unreachable';
  return 'other';
}

const HOLD_WORDS: Record<HoldCause, string> = {
  never_confirmed: 'Never confirmed: nobody has confirmed that this is the robot, this resident session and this host.',
  resident_restarted: 'The resident restarted: the robot\'s Cake resident reports a different session than the one confirmed.',
  host_restarted: 'The host changed: the supervised robot host is not the host instance that was confirmed.',
  host_unreadable: 'The supervised robot host cannot be read, so nothing says it is the host instance that was confirmed.',
  identity_changed: 'A resident that does not report the pinned build answered after the confirmation, so the confirmation was revoked.',
  not_the_robot: 'Not the pinned build: the resident this runtime reads over its robot connection reports a build other than the one pinned for the robot.',
  unreachable: 'The robot\'s resident cannot be read, so nothing says it is still the session that was confirmed.',
  other: 'The runtime holds motion for a reason this console has no words for.',
};

/** Which part of the host instance changed, when the runtime says; said beside the cause, never instead of it. */
const CHANGE_WORDS: Record<ChangedComponent, string> = {
  session: 'the resident session changed',
  slot_instance: 'the supervisor slot was re-created (its instance handle changed)',
  spawn_sequence: 'the supervisor started the host again (its spawn sequence changed, whatever the pid)',
  pid: 'a different host process runs under the slot',
  host_stopped: 'no host runs under the slot now (stopped, held or degraded)',
  identity: 'a resident that does not report the pinned build answered',
};

export interface MotionPermissionView {
  /** False when no robot resident gates motion here at all. The runtime's other checks still apply. */
  applies: boolean;
  /** True only for an explicit permitted: true on a current reading. */
  permitted: boolean;
  /** True when a command that moves the robot is held on this account. */
  held: boolean;
  cause: HoldCause | null;
  /** The cause, in one sentence. */
  why: string | null;
  /** What changed, when a confirmation was revoked by a change; null otherwise. */
  changed: string | null;
  code: string | null;
  /** The runtime's own sentence, unaltered. */
  detail: string | null;
  /** What the confirm action would post, EXACTLY as displayed. */
  currentSession: string | null;
  hostInstance: HostInstance | null;
  confirmedSession: string | null;
  confirmedHostInstance: HostInstance | null;
  confirmedWall: string | null;
  /** Why confirming is not possible from this reading, or null when it may be offered. */
  confirmOff: string | null;
}

/** A host instance in words: the three things a confirmation names, in the order the runtime compares them. */
export function hostInstanceWords(instance: HostInstance | null): string {
  if (instance === null) return 'not identified';
  return `slot instance ${instance.slot_instance}, spawn ${String(instance.spawn_sequence)}, pid ${String(instance.pid)}`;
}

/** Two host instances are the same only when every component is. */
export function sameHostInstance(a: HostInstance | null, b: HostInstance | null): boolean {
  if (a === null || b === null) return a === b;
  return a.slot_instance === b.slot_instance && a.spawn_sequence === b.spawn_sequence && a.pid === b.pid;
}

/** What an open confirmation dialog displays: taken when it opened, and the only values it can post. */
export interface ConfirmShown {
  session: string;
  instance: HostInstance;
}

/**
 * Why the DIALOG's confirm is off, or null. Every reason the banner's button is off (`bannerOff`, re-evaluated on each
 * reading), and one of its own: the reading no longer shows what the dialog displays. A dialog never posts a value the
 * robot does not report now, and never a value other than the one it showed.
 */
export function confirmDialogOff(bannerOff: string | null, shown: ConfirmShown | null, permission: MotionPermissionView): string | null {
  if (bannerOff !== null) return bannerOff;
  if (shown === null) return 'nothing is displayed to confirm';
  if (shown.session !== permission.currentSession || !sameHostInstance(shown.instance, permission.hostInstance)) {
    return 'the robot now reports a different session or host instance than this dialog shows';
  }
  return null;
}

const HOST_NOT_OFFERED: Record<string, string> = {
  robot_host_not_running: 'no host is running under the supervisor slot (pid 0, held or degraded), so there is no host to confirm',
  robot_host_unreadable: 'the host under the supervisor slot could not be identified (slot instance, spawn sequence and pid), so it cannot be confirmed',
};

export function motionPermissionView(read: RobotReadState, nowMs: number): MotionPermissionView {
  const status = read.status;
  const empty: MotionPermissionView = {
    applies: false, permitted: false, held: false, cause: null, why: null, changed: null, code: null, detail: null,
    currentSession: null, hostInstance: null, confirmedSession: null, confirmedHostInstance: null, confirmedWall: null,
    confirmOff: 'no robot resident is configured, so there is no robot session to confirm',
  };
  if (status === null) return { ...empty, confirmOff: 'the robot status has not been read' };
  const motion = status.motion_permission;
  if (!motion.applies || !status.configured) {
    return { ...empty, code: motion.reason_code, detail: motion.detail };
  }
  const link = robotLink(read, nowMs);
  // A PERMISSION READ FROM A RETAINED ANSWER IS NOT A CURRENT PERMISSION.
  const permitted = motion.permitted === true && !link.stale;
  const code = motion.permitted === true && link.stale ? 'robot_status_not_current' : motion.reason_code;
  const cause = permitted ? null : holdCause(code);
  const currentSession = motion.current_session_uuid;
  const instance = motion.current_host_instance;
  // OFFERED ONLY FOR A HOST INSTANCE THAT RUNS AND WAS IDENTIFIED, on a current reading, over the robot connection, of a
  // resident reporting the pinned build. Never for pid 0, a held or DEGRADED supervisor slot, another build or a
  // retained reading.
  const confirmOff = link.state === 'not_the_robot'
    ? 'the resident answering does not report the build pinned for the robot, so nothing it reports can be confirmed'
    : link.stale || link.state !== 'answering'
      ? 'the robot is not answering now; a retained session is never confirmed'
      : currentSession === null
        ? 'the runtime reports no current resident session to confirm'
        : instance === null || !motion.confirmable
          ? HOST_NOT_OFFERED[motion.current_host_reason_code ?? ''] ??
            `the runtime reports no running, identified host to confirm (${motion.current_host_reason_code ?? 'no reason was given'})`
          : null;
  return {
    applies: true,
    permitted,
    held: !permitted,
    cause,
    why: cause === null ? null : code === 'robot_status_not_current' ? HOLD_WORDS.unreachable : HOLD_WORDS[cause],
    changed: permitted || motion.changed_component === null ? null : CHANGE_WORDS[motion.changed_component],
    code,
    detail: motion.detail,
    currentSession,
    hostInstance: confirmOff === null ? instance : null,
    confirmedSession: motion.confirmed_session_uuid,
    confirmedHostInstance: motion.confirmed_host_instance,
    confirmedWall: motion.confirmed_wall_utc,
    confirmOff,
  };
}

/* ---- recovery: a component restarted, or the whole resident did ------------------------------------------------- */

export interface RecoveryView {
  /** The resident's session changed: every slot with it, and motion permission is revoked. Null is not determined. */
  residentRestarted: boolean | null;
  residentSentence: string;
  /** Supervisor restarts of the host inside the current resident session, from get-health. Null when not reported. */
  hostRestarts: number | null;
  /** Supervisor restarts of the capability's process inside the current resident session. */
  capabilityRestarts: number | null;
  componentSentence: string;
  /** True when both sentences rest on a RETAINED reading; they are then drawn STALE with its age, never as current. */
  stale: boolean;
  ageMs: number | null;
}

function healthRowFor(status: RobotStatus, slotUuid: string | null, childPid: number | null): RobotHealthRow | null {
  return (
    status.health.items.find((row) => slotUuid !== null && row.slot === slotUuid) ??
    status.health.items.find((row) => childPid !== null && childPid > 0 && row.child_pid === childPid) ??
    null
  );
}

/**
 * What restarted, from one robot reading. `link` says whether that reading is current: a retained one is marked stale
 * with its age, so a recovery sentence is never read as a statement about now.
 */
export function recoveryView(status: RobotStatus | null, link: RobotLink | null = null): RecoveryView {
  if (status === null || !status.configured) {
    return {
      residentRestarted: null,
      residentSentence: 'No robot resident is read, so no restart can be seen.',
      hostRestarts: null,
      capabilityRestarts: null,
      componentSentence: 'No component restart can be seen without a robot reading.',
      stale: false,
      ageMs: null,
    };
  }
  const stale = link?.stale === true || status.stale || status.resident.stale;
  const changed = status.resident.session_changed;
  const motion = status.motion_permission;
  // A RESIDENT RESTART IS STICKY FOR THE RUNTIME'S LIFE, and it is not a permanent hold: once the operator confirmed
  // the session the resident reports now, the restart is history, and the sentence says so instead of a hold.
  const reconfirmed =
    motion.confirmed_session_uuid !== null && motion.confirmed_session_uuid === status.resident.session_uuid && motion.permitted === true;
  const residentSentence =
    changed === true
      ? reconfirmed
        ? 'Resident restart seen earlier in this runtime: the robot\'s Cake resident reported a different session than the one this runtime first read. The session it reports now has since been confirmed.'
        : 'Resident restart: the robot\'s Cake resident now reports a different session than the one this runtime first read. Every slot restarted with it, and motion stays held until the session it reports now is confirmed.'
      : changed === false
        ? 'No resident restart: the same resident session since this runtime first read it.'
        : 'Resident restart not determined yet: it takes two readings to compare.';
  const host = healthRowFor(status, status.host.slot_uuid, status.host.child_pid);
  const capabilitySlot = status.slots.items.find((row) => row.name === status.capability.name)?.uuid ?? null;
  const capability = status.capability.name === null ? null : healthRowFor(status, capabilitySlot, status.capability.child_pid);
  const hostRestarts = host?.restarts ?? null;
  const capabilityRestarts = capability?.restarts ?? null;
  const parts: string[] = [];
  if (hostRestarts !== null) {
    parts.push(
      hostRestarts === 0
        ? 'the supervised host has not been restarted in this resident session'
        : (status.host.child_pid ?? 0) > 0
          ? `the supervisor restarted its child ${String(hostRestarts)} time(s) in this resident session; the host is running now`
          : `the supervisor restarted its child ${String(hostRestarts)} time(s) in this resident session and the host is NOT running now; a start can be refused, for example for lack of an operator arm token, without the host ever running`,
    );
  }
  if (capabilityRestarts !== null) {
    parts.push(
      capabilityRestarts === 0
        ? `${status.capability.name ?? 'the capability'} has not been restarted`
        : `the supervisor restarted ${status.capability.name ?? 'the capability'} ${String(capabilityRestarts)} time(s)`,
    );
  }
  const componentSentence =
    parts.length === 0
      ? 'Component recoveries not reported: get-health gave no row for the host or the capability.'
      : `Component recovery: ${parts.join('; ')}. A component recovery keeps the resident session; it is not a resident restart. A host restart still revokes motion permission, because the host instance changed.`;
  return {
    residentRestarted: changed,
    residentSentence,
    hostRestarts,
    capabilityRestarts,
    componentSentence,
    stale,
    ageMs: stale ? status.resident.observed?.age_ms ?? null : null,
  };
}
