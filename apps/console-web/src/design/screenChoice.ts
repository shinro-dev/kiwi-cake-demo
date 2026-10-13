// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHICH SCREEN THE PAGE OPENS ON. The show screen (the four-click recorded demo, operator ruling 2026-10-11) is the
// default; the earlier demo screen stays behind `?screen=demo` and the classic screen behind `?screen=classic`, each
// named exactly. Any other value is the default: an address never lands on a screen by accident.

export type ScreenChoice = 'show' | 'demo' | 'classic';

export function screenChoiceFrom(search: string): ScreenChoice {
  const value = new URLSearchParams(search).get('screen');
  return value === 'classic' ? 'classic' : value === 'demo' ? 'demo' : 'show';
}
