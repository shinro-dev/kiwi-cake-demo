// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// ONE NAME FOR THE MODE (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 5.2; audit C12): Live, Rehearsal,
// Recording, or Mode not recorded. The mark appears once, in the top bar; History and Diagnostics use the same words.

export type ModeId = 'live' | 'rehearsal' | 'recording' | 'not_recorded';

export interface ModeMark {
  id: ModeId;
  word: string;
}

export function modeMark(input: { dataMode: string | null; sourceKind: 'live' | 'synthetic'; replaying: boolean }): ModeMark {
  if (input.replaying) return { id: 'recording', word: 'Recording' };
  if (input.sourceKind === 'synthetic' || input.dataMode === 'synthetic') return { id: 'rehearsal', word: 'Rehearsal' };
  if (input.dataMode === 'live') return { id: 'live', word: 'Live' };
  return { id: 'not_recorded', word: 'Mode not recorded' };
}
