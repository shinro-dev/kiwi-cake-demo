// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SENTENCE THIS CONSOLE STATES ABOUT CAKE BESIDE THE TASK, read from the mission record.
//
// It was one constant, "Cake: not connected to this task", pinned so that wiring up a mission could not change it as a
// side effect (`design/screen.test.ts`). The one-block demo is a mission that WAITS on a Cake activation and RESUMES
// through one (docs/autonomous-demo/one-block-demo-2026-09-26.md, T4), and the constant is false for the whole of that
// mission. So the sentence now reads what the mission record says, and says the constant only where the constant is
// true: no mission, or a mission that never waited on Cake and recorded no activation.
//
// THIS DECIDES NOTHING ABOUT AN ACTIVATION. Which activation state reads as activated is `design/cake.ts`'s
// `activationLine`, and this reads the same rule: OBSERVED and nothing else. A rejected, failed or unobserved
// activation is an attempt, said as one.

import type { MissionRecord } from './missionRecord';

/** The sentence this demo states about Cake beside a task Cake took no part in, exactly as it is to be stated. */
export const CAKE_TASK_SENTENCE = 'Execution: Python baseline. Cake: not connected to this task.';

const EXECUTION = 'Execution: Python baseline.';

/**
 * WHETHER A CAKE MODULE TOOK PART IN A MISSION, read from the mission's own record by the runtime's rule
 * (`demo_runtime/cake_admin.py`, mission_participation): a target's capability evidence with a generation, a pid and at
 * least one base command the motion gateway APPLIED through it, or an activation recorded OBSERVED. Nothing else counts,
 * and an admin socket cannot say it.
 */
export interface CakeParticipation {
  value: boolean;
  /** A sentence naming what it rests on, or why it is false. */
  evidence: string;
  basis: 'capability_applied' | 'activation_observed' | null;
  module: string | null;
  generation: number | null;
  pid: number | null;
  applied: number | null;
  target_id: string | null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function plainCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The runtime's participation rule, applied to a mission record as it arrived (targets carry `evidence.capability`). */
export function participationFromMission(raw: unknown, moduleName: string | null): CakeParticipation {
  const out: CakeParticipation = {
    value: false,
    evidence: 'no mission is on record, so no module of any resident is carrying part of a task',
    basis: null,
    module: moduleName,
    generation: null,
    pid: null,
    applied: null,
    target_id: null,
  };
  if (!isObject(raw)) return out;
  const named = moduleName ?? 'the capability module';
  for (const target of Array.isArray(raw['targets']) ? raw['targets'] : []) {
    if (!isObject(target) || !isObject(target['evidence'])) continue;
    const capability = target['evidence']['capability'];
    if (!isObject(capability)) continue;
    const generation = plainCount(capability['generation']);
    const pid = plainCount(capability['pid']);
    const applied = plainCount(capability['applied']);
    if (generation === null || pid === null || applied === null || applied === 0) continue;
    const targetId = typeof target['target_id'] === 'string' ? target['target_id'] : null;
    return {
      value: true,
      basis: 'capability_applied',
      module: moduleName,
      generation,
      pid,
      applied,
      target_id: targetId,
      evidence: `${named} (generation ${String(generation)}, pid ${String(pid)}) carried the base for target ${targetId ?? 'unnamed'}: ${String(applied)} base commands relayed through it were applied by the motion gateway`,
    };
  }
  const activations = Array.isArray(raw['activations']) ? raw['activations'] : [];
  const observed = activations.filter((item): item is Record<string, unknown> => isObject(item) && item['activation_state'] === 'OBSERVED');
  const newest = observed[observed.length - 1];
  if (newest !== undefined) {
    const key = typeof newest['candidate_key'] === 'string' ? `candidate ${newest['candidate_key']}` : 'a prepared candidate';
    return { ...out, value: true, basis: 'activation_observed', evidence: `a Cake activation for this mission (${key}) was read back in the capability slot carrying ${named}` };
  }
  return { ...out, evidence: 'this mission\'s record shows no base command applied through a Cake module and no activation observed in the capability slot' };
}

export function cakeTaskSentence(mission: MissionRecord | null, participation: CakeParticipation | null = null): string {
  if (mission === null) return CAKE_TASK_SENTENCE;
  // THE RECORD SAYS A CAKE MODULE CARRIED PART OF THIS TASK: said first, with what it rests on.
  if (participation !== null && participation.value && participation.basis === 'capability_applied') {
    return `${EXECUTION} Cake: ${participation.evidence}.`;
  }
  if (mission.state === 'WAITING_FOR_CAPABILITY') {
    return `${EXECUTION} Cake: this mission is held for a capability the resident does not hold; the activation request in the Cake panel is what lets it go on.`;
  }
  const observed = mission.activations.filter((activation) => activation.activation_state === 'OBSERVED');
  const newest = observed[observed.length - 1];
  if (newest !== undefined) {
    const candidate = newest.candidate_key === null ? 'a prepared candidate' : `candidate ${newest.candidate_key}`;
    return `${EXECUTION} Cake: this mission resumed through a Cake activation (${candidate}): the capability slot was read back carrying the capability.`;
  }
  const attempts = mission.activations.length;
  if (attempts > 0) {
    return `${EXECUTION} Cake: ${String(attempts)} activation${attempts === 1 ? ' was' : 's were'} attempted for this mission and none was observed in the slot.`;
  }
  return CAKE_TASK_SENTENCE;
}
