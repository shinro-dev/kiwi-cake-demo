// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE MODULE CARDS OF THE CAKE PANEL, built from what the robot's resident reports and from nothing else.
//
// One card per slot the resident lists (`robot_status` slots.items), joined by slot uuid to the row its get-health
// answer gave (health.items: arg1 is the child pid, sequence the spawns, flags the restarts), plus ONE card that is not
// a Cake module at all: the controller. Every card names its REVISION as the resident identifies it (the capsule digest
// and the slot's generation) and never as a software version: a slot has no version number, and printing one would be
// this console inventing it (the decision log's rule: "Use the fields Cake actually has").
//
// WHAT A CARD DOES NOT CARRY. The slot's config digest is printed by admin-probe but not carried by `robot_status` v2
// (its slot row admits six fields), so a card says it is not reported rather than leaving it out silently.
//
// It DECIDES NOTHING about the capability: whether it is active is the runtime's `capability.active`, and whether the
// host is held is the capability slot check's `held`. A card only lays out what was read.

import type { RobotHealthRow, RobotHost, RobotSlotRow } from '../model/robotStatus';

export type ModuleKind = 'capability' | 'holder' | 'supervisor' | 'module' | 'controller';

export interface ModuleCard {
  name: string;
  kind: ModuleKind;
  slot_uuid: string | null;
  capsule_digest: string | null;
  /** Not carried by robot_status v2; null says "not reported by this surface", never an empty config. */
  config_digest: string | null;
  generation: number | null;
  /** The slot instance handle, a decimal string. */
  instance: string | null;
  /** The slot's lifecycle word as the resident prints it (ACTIVE, ...). */
  state: string | null;
  /** The health row's state name (HEALTHY, DEGRADED, ...), null with no row. */
  health: string | null;
  /** A pid ON THE ROBOT: the slot's child process. 0 is "no child now". */
  pid: number | null;
  spawns: number | null;
  restarts: number | null;
  /** Who starts and stops this process. */
  managed_by: string;
  /** A sentence that has to travel with the card, or null. */
  note: string | null;
}

/** The name the resident gives the slot that supervises the motor host. */
export const SUPERVISOR_SLOT_NAME = 'supervisor.lekiwi';

/** The sentence the controller's card carries, exactly. */
export const CONTROLLER_NOTE = 'content-addressed, not signed, not a Cake module';

function kindOf(row: RobotSlotRow, capabilitySlot: string | null): ModuleKind {
  if (row.name === SUPERVISOR_SLOT_NAME) return 'supervisor';
  if (capabilitySlot !== null && row.uuid !== null && row.uuid.toLowerCase() === capabilitySlot.toLowerCase()) return 'capability';
  if (row.name.endsWith('.holder')) return 'holder';
  return 'module';
}

const MANAGED_BY: Record<Exclude<ModuleKind, 'controller'>, string> = {
  supervisor: 'the Cake resident, as a slot; its child is the supervised motor host',
  capability: 'the Cake resident, as a slot',
  holder: 'the Cake resident, as a slot',
  module: 'the Cake resident, as a slot',
};

const NOTES: Record<Exclude<ModuleKind, 'controller'>, string | null> = {
  supervisor: 'the motor host runs as this slot\'s child only when the supervisor spawns it; pid 0 is no host process now',
  // How an activation works (demo_runtime/cake_activation.py), never what the mission is doing now (verifier r2 defect 10).
  capability: 'an activation moves this slot and its holder slot together',
  // Said as the resident lists it and nothing more (audit C9): its capsule, generation and health are on the card.
  holder: null,
  module: null,
};

/** One card per listed slot, in the resident's name order, joined to its health row by slot uuid. */
export function moduleCards(slots: readonly RobotSlotRow[], health: readonly RobotHealthRow[], capabilitySlot: string | null = null): ModuleCard[] {
  return [...slots]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((row) => {
      const kind = kindOf(row, capabilitySlot);
      const health_row = row.uuid === null ? undefined : health.find((item) => item.slot !== null && item.slot.toLowerCase() === row.uuid?.toLowerCase());
      return {
        name: row.name,
        kind,
        slot_uuid: row.uuid,
        capsule_digest: row.capsule,
        config_digest: null,
        generation: row.generation,
        instance: row.instance,
        state: row.state,
        health: health_row?.state_name ?? null,
        pid: health_row?.child_pid ?? null,
        spawns: health_row?.spawns ?? null,
        restarts: health_row?.restarts ?? null,
        managed_by: MANAGED_BY[kind === 'controller' ? 'module' : kind],
        note: NOTES[kind === 'controller' ? 'module' : kind],
      };
    });
}

/**
 * THE CONTROLLER'S CARD. It is a supervised child of this runtime, identified by its bundle digest when the runtime
 * reports one. It is not a Cake module, it is not signed, and it says so on its face.
 */
export function controllerCard(bundleDigest: string | null, pid: number | null = null): ModuleCard {
  return {
    name: 'controller',
    kind: 'controller',
    slot_uuid: null,
    capsule_digest: bundleDigest,
    config_digest: null,
    generation: null,
    instance: null,
    state: null,
    health: null,
    pid,
    spawns: null,
    restarts: null,
    managed_by: 'this runtime, as a supervised child run',
    note: CONTROLLER_NOTE,
  };
}

function short(digest: string | null): string {
  return digest === null ? 'not reported' : digest.replace(/^sha256:/, '').slice(0, 12);
}

/** The card's identity as a Revision: the capsule (or bundle) digest and the generation. Never a version number. */
export function revisionWords(card: ModuleCard): string {
  if (card.kind === 'controller') return `Revision: bundle ${short(card.capsule_digest)}`;
  return `Revision: capsule ${short(card.capsule_digest)}, generation ${card.generation === null ? 'not reported' : String(card.generation)}`;
}

/** The process line: state, health, pid, spawns and restarts, each as read or "not reported", never a zero for absent. */
export function processWords(card: ModuleCard): string {
  const parts: string[] = [];
  if (card.state !== null) parts.push(`slot ${card.state}`);
  if (card.health !== null) parts.push(`health ${card.health}`);
  parts.push(card.pid === null ? 'pid not reported' : card.pid === 0 ? 'no child process now (pid 0)' : `pid ${String(card.pid)}`);
  if (card.kind !== 'controller') {
    parts.push(`spawns ${card.spawns === null ? 'not reported' : String(card.spawns)}`);
    parts.push(`restarts ${card.restarts === null ? 'not reported' : String(card.restarts)}`);
  }
  return parts.join(' | ');
}

/**
 * THE HONESTY LINES the panel carries whenever it draws a candidate: what a live update is and is not here, and what
 * each refusal it can show is. They are the adversarial checks' wording rules, on the screen.
 */
export const CAKE_HONESTY_LINES: readonly string[] = [
  'A live update here selects among capsules the resident admitted when it started; the resident has no live admission operation.',
  'Abort-swap is a transactional refusal: Cake opens the transaction and aborts it, and the slot is left as it was.',
  'The tampered twin is an integrity refusal: one flipped byte breaks the Plan\'s digests, and Cake refuses it before any transaction. It is not a signature refusal.',
  `The controller is ${CONTROLLER_NOTE}.`,
];

/**
 * A MOTOR HOST RUNNING OUTSIDE CAKE: the robot computer's readiness probe finds a running host while the supervisor slot
 * reports no child of its own (no slot, a slot not ACTIVE, child pid 0), or a child whose pid is not the host that runs.
 * That is a host someone started by hand, which nothing on the screen named during the bring-up of 2026-09-26. Null
 * when the readings do not say so, including when they could not be read: not read is not a finding.
 */
export interface HostFinding {
  code: 'host_outside_cake';
  sentence: string;
}

const RUNNING_READINESS = new Set(['running_normal', 'scan_mode']);
const NOT_CAKES = new Set(['supervisor_slot_absent', 'supervisor_slot_not_active', 'supervisor_child_not_running', 'host_pid_mismatch']);

export function hostOutsideCake(host: RobotHost | null): HostFinding | null {
  if (host === null || host.readiness_state === null || !RUNNING_READINESS.has(host.readiness_state)) return null;
  if (host.reason_code === null || !NOT_CAKES.has(host.reason_code)) return null;
  const why =
    host.reason_code === 'host_pid_mismatch'
      ? `the supervisor's child is pid ${host.child_pid === null ? 'not reported' : String(host.child_pid)}, which is not the host that runs`
      : host.reason_code === 'supervisor_child_not_running'
        ? 'the supervisor slot reports no child of its own (pid 0)'
        : host.reason_code === 'supervisor_slot_not_active'
          ? 'the supervisor slot is not ACTIVE'
          : 'the resident lists no supervisor slot';
  return {
    code: 'host_outside_cake',
    sentence: `A motor host is running on the robot computer that Cake did not start: the readiness probe finds one running, and ${why}. Stop it before arming the supervised host.`,
  };
}
