// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import { describe, expect, it } from 'vitest';
import { screenChoiceFrom } from './screenChoice';

describe('which screen the page opens on', () => {
  it('is the show screen by default', () => {
    expect(screenChoiceFrom('')).toBe('show');
    expect(screenChoiceFrom('?source=simulation&scenario=one-block')).toBe('show');
  });

  it('is the earlier demo screen or the classic screen only when the address asks by name', () => {
    expect(screenChoiceFrom('?screen=classic')).toBe('classic');
    expect(screenChoiceFrom('?screen=demo')).toBe('demo');
    expect(screenChoiceFrom('?source=simulation&screen=classic')).toBe('classic');
    expect(screenChoiceFrom('?screen=CLASSIC')).toBe('show');
    expect(screenChoiceFrom('?screen=anything')).toBe('show');
  });
});
