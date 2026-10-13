// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE FIVE STAGE FILES, READ AS ONE. failure.ts, reason.ts and phase.ts build their tables from these two maps, and a
// test (stages.test.ts) holds the maps equal, key for key, to the tables those three files held before the split. A
// code or a state named by two stage files is a mistake that test reports; here the first stage in the order below
// keeps it, so the screen never depends on the order an object's keys were spread in.

import { ALIGN_STAGE } from './align';
import { GRASP_STAGE } from './grasp';
import { PLACEMENT_STAGE } from './placement';
import { RECOVERY_STAGE } from './recovery';
import { SEARCH_STAGE } from './search';
import type { CodeTemplate, StageGlossary, StageId, StageState } from './stage';

export const STAGE_IDS: readonly StageId[] = ['search', 'align', 'grasp', 'placement', 'recovery'];

/** The stage files in the order a run passes through them. */
export const STAGE_GLOSSARIES: readonly StageGlossary[] = [SEARCH_STAGE, ALIGN_STAGE, GRASP_STAGE, PLACEMENT_STAGE, RECOVERY_STAGE];

function merged<T>(pick: (glossary: StageGlossary) => Readonly<Record<string, T>>): Readonly<Record<string, T>> {
  const out: Record<string, T> = {};
  for (const glossary of STAGE_GLOSSARIES) {
    for (const [key, value] of Object.entries(pick(glossary))) if (!Object.hasOwn(out, key)) out[key] = value;
  }
  return out;
}

/** Every controller failure code a stage glosses. */
export const STAGE_FAILURES: Readonly<Record<string, CodeTemplate>> = merged((glossary) => glossary.failures);

/** Every controller state a stage names, with its sentence, phase and presentation step. */
export const STAGE_STATES: Readonly<Record<string, StageState>> = merged((glossary) => glossary.states);
