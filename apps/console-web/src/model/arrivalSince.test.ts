// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// WHEN A RUN OF ARRIVALS BEGAN (browser r2 N4, verifier R-2): Diagnostics says a feed that keeps arriving as a rate and
// the fixed time it began, never the time of its newest sample, so nothing on the tab moves while nothing changes.

import { describe, expect, it } from 'vitest';
import { arrivalSince } from './arrivalSince';

describe('the time a run of arrivals began', () => {
  it('starts at the first arrival', () => {
    expect(arrivalSince(null, null, 1_000, 4_000)).toBe(1_000);
  });

  it('keeps its start while each arrival follows the last within the gap', () => {
    let since = arrivalSince(null, null, 1_000, 4_000);
    since = arrivalSince(since, 1_000, 1_100, 4_000);
    since = arrivalSince(since, 1_100, 5_000, 4_000);
    expect(since).toBe(1_000);
  });

  it('restarts after a silence longer than the gap', () => {
    expect(arrivalSince(1_000, 2_000, 6_001, 4_000)).toBe(6_001);
  });
});
