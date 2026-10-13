// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ROBOT, IN THE DESIGN'S VOCABULARY: the header's identity, the Cake panel's rows, and their tones.
//
// `model/robotStatus.ts` reads the surface and decides what is current, retained, held and restarted. This file only
// words and colours those decisions for the imported composition, so no panel invents its own reading of a robot.
// Nothing here reads `/api/cake`: that surface does not say which machine answered, so it never speaks for the robot.

import type { MotionPermissionView, RobotLink, RobotStatus } from '../model/robotStatus';
import { C, INK, wash } from './theme';

export interface RobotChip {
  label: string;
  dot: string;
  fg: string;
  bg: string;
  border: string;
}

export function linkTone(link: RobotLink): string {
  switch (link.state) {
    case 'answering':
      return C.ok;
    case 'not_answering':
    case 'not_the_robot':
      return C.bad;
    case 'not_read':
      return C.warn;
    default:
      return C.mute;
  }
}

/** The Cake panel's chip: the robot's resident, bound to GET /api/robot and to nothing synthetic. */
export function robotChip(link: RobotLink): RobotChip {
  if (link.state === 'answering') {
    return { label: 'Robot resident answering', dot: C.ok, fg: '#a7f3d0', bg: wash(C.ok, 0.1), border: wash(C.ok, 0.32) };
  }
  if (link.state === 'not_answering') {
    return { label: 'Robot not answering', dot: C.warn, fg: '#fde68a', bg: wash(C.warn, 0.1), border: wash(C.warn, 0.3) };
  }
  if (link.state === 'not_the_robot') {
    // The state keeps its name. What it establishes is that another BUILD answered, not that another machine did.
    return { label: 'Not the pinned build', dot: C.bad, fg: '#fecaca', bg: wash(C.bad, 0.1), border: wash(C.bad, 0.32) };
  }
  if (link.state === 'not_read') {
    return { label: 'Robot not read', dot: C.warn, fg: '#fde68a', bg: wash(C.warn, 0.1), border: wash(C.warn, 0.3) };
  }
  return { label: 'No robot configured', dot: INK.faint, fg: INK.quiet, bg: '#13131a', border: 'var(--color-border)' };
}

/** One row of the Cake panel. `stale` rows carry the STALE tag and their age; they are never drawn as current. */
export interface RobotRow {
  key: string;
  label: string;
  value: string;
  tone: string;
  note: string | null;
  stale: boolean;
  /** The retained reading's age on the runtime's clock, when it is retained. */
  ageMs: number | null;
}

/** Words for the reason codes the runtime gives a host that is not supervised. Unknown codes are shown as written. */
const HOST_REASON: Record<string, string> = {
  host_pid_mismatch: 'the running host is not the process the supervisor started',
  robot_not_configured: 'no robot is configured',
};

export function residentRow(status: RobotStatus, link: RobotLink): RobotRow {
  const resident = status.resident;
  const session = resident.session_uuid;
  if (resident.observed === null) {
    return {
      key: 'resident', label: 'Cake resident', value: 'not reached', tone: C.warn,
      note: `configured, and it has not answered: ${resident.reason_code ?? status.reason_code ?? 'no reason was given'}`,
      stale: false, ageMs: null,
    };
  }
  if (link.stale || resident.stale || !resident.reachable) {
    return {
      key: 'resident', label: 'Cake resident', value: 'last seen', tone: C.warn,
      note: `its last answer, session ${session ?? 'not reported'}`, stale: true, ageMs: resident.observed.age_ms,
    };
  }
  return {
    key: 'resident', label: 'Cake resident', value: 'answering', tone: C.ok,
    note: `session ${session ?? 'not reported'}${resident.epoch === null ? '' : `, epoch ${String(resident.epoch)}`}`,
    stale: false, ageMs: null,
  };
}

export function hostRow(status: RobotStatus, link: RobotLink): RobotRow {
  const host = status.host;
  if (link.stale) {
    // SUPERVISION IS NEVER TAKEN FROM A RETAINED READING, and the runtime already refuses to. The row says it is not
    // current rather than repeating the last answer as if it still stood.
    return {
      key: 'host', label: 'Supervised host', value: 'not current', tone: C.warn,
      // The age of the retained RESIDENT reading: the host's own readiness age is a different probe's, and is not it.
      note: 'supervision is never read from a retained answer', stale: true, ageMs: status.resident.observed?.age_ms ?? null,
    };
  }
  if (host.supervised) {
    return {
      key: 'host', label: 'Supervised host', value: 'supervised, running', tone: C.ok,
      note: `pid ${host.child_pid === null ? 'not reported' : String(host.child_pid)} on the robot, slot ${host.slot_state ?? 'state not reported'}`,
      stale: false, ageMs: null,
    };
  }
  const code = host.reason_code;
  return {
    key: 'host', label: 'Supervised host', value: 'not supervised', tone: C.bad,
    note: code === null ? 'the runtime did not say why' : HOST_REASON[code] ?? code,
    stale: false, ageMs: null,
  };
}

export function capabilityRow(status: RobotStatus, link: RobotLink): RobotRow {
  const capability = status.capability;
  if (!capability.configured) {
    return { key: 'capability', label: 'Capability', value: 'none configured', tone: C.mute, note: null, stale: false, ageMs: null };
  }
  const name = capability.name ?? 'name not reported';
  const identity = `generation ${capability.generation === null ? 'not reported' : String(capability.generation)}, pid ${
    capability.child_pid === null ? 'not reported' : String(capability.child_pid)
  }`;
  if (link.stale || capability.stale) {
    return {
      key: 'capability', label: name, value: capability.active ? 'last seen active' : 'last seen inactive', tone: C.warn,
      note: 'a retained listing never makes a capability active', stale: true, ageMs: capability.observed?.age_ms ?? null,
    };
  }
  if (capability.active) {
    return { key: 'capability', label: name, value: 'active', tone: C.ok, note: identity, stale: false, ageMs: null };
  }
  return {
    key: 'capability', label: name, value: 'not active', tone: C.wait,
    note: capability.reason_code ?? 'the runtime did not say why', stale: false, ageMs: null,
  };
}

/** The newest update record the resident's flight ring shows, worded, or null when there is none. */
export function lastUpdateWords(status: RobotStatus): string | null {
  const newest = status.updates.items[status.updates.items.length - 1] ?? null;
  if (newest === null) return null;
  return `${newest.name} (${newest.kind}${newest.sequence === null ? '' : `, record ${String(newest.sequence)}`}${
    newest.epoch === null ? '' : `, epoch ${String(newest.epoch)}`
  })`;
}

/** The hold banner's tone: a hold is the held colour, a permitted robot draws no banner at all. */
export function holdTone(permission: MotionPermissionView): string {
  return permission.cause === 'unreachable' ? C.warn : C.wait;
}
