// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHICH SCREEN THE PAGE OPENS ON. The simplified demo screen is the default; the classic screen is kept behind
// `?screen=classic`, named exactly, so an operator who needs a panel it still carries can reach it. Any other value is
// the default: an address never lands on a screen by accident.

export type ScreenChoice = 'demo' | 'classic';

export function screenChoiceFrom(search: string): ScreenChoice {
  return new URLSearchParams(search).get('screen') === 'classic' ? 'classic' : 'demo';
}
