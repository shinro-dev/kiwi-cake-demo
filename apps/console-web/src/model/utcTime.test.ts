// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// TIMES ON THE DEMO SCREEN (spec section 5.4): absolute UTC always, the date only when it is not the reference day, the
// full ISO string with milliseconds for the title and `data-t`, fractional seconds of any length, durations only between
// two recorded times, and nothing measured against this display's clock. The values are the live record of 2026-09-27.

import { describe, expect, it } from 'vitest';
import { durationWords, parseUtcMs, utcDayOf, utcStamp } from './utcTime';

describe('the UTC times of the demo screen', () => {
  it('parses fractional seconds of any length, truncated to milliseconds', () => {
    expect(parseUtcMs('2026-09-27T14:21:46.010Z')).toBe(Date.UTC(2026, 8, 27, 14, 21, 46, 10));
    expect(parseUtcMs('2026-09-27T14:21:50.070538Z')).toBe(Date.UTC(2026, 8, 27, 14, 21, 50, 70));
    expect(parseUtcMs('2026-09-27T14:21:50.572000Z')).toBe(Date.UTC(2026, 8, 27, 14, 21, 50, 572));
    expect(parseUtcMs('2026-09-27T14:21:50Z')).toBe(Date.UTC(2026, 8, 27, 14, 21, 50, 0));
    expect(parseUtcMs('2026-09-27T14:21:50.5+00:00')).toBe(Date.UTC(2026, 8, 27, 14, 21, 50, 500));
  });

  it('reads nothing into a value that is not a UTC time', () => {
    expect(parseUtcMs(null)).toBeNull();
    expect(parseUtcMs('')).toBeNull();
    expect(parseUtcMs('yesterday')).toBeNull();
    // A local time with no zone says nothing about UTC and is not read as one.
    expect(parseUtcMs('2026-09-27T14:21:50')).toBeNull();
  });

  it('prints HH:MM:SSZ on the reference day and the date on any other, with the full ISO string as its title', () => {
    const stamp = utcStamp('2026-09-27T14:24:40.114Z', '2026-09-27');
    expect(stamp).toEqual({ text: '14:24:40Z', iso: '2026-09-27T14:24:40.114Z', title: '2026-09-27T14:24:40.114Z' });
    expect(utcStamp('2026-09-26T20:26:13.4Z', '2026-09-27')?.text).toBe('2026-09-26 20:26:13Z');
    expect(utcStamp('2026-09-27T14:21:50.070538Z', null)?.text).toBe('2026-09-27 14:21:50Z');
    expect(utcStamp('2026-09-27T14:21:50.070538Z', null)?.iso).toBe('2026-09-27T14:21:50.070Z');
    expect(utcStamp(null, '2026-09-27')).toBeNull();
  });

  it('names the UTC day of a recorded time', () => {
    expect(utcDayOf('2026-09-27T23:59:59.999Z')).toBe('2026-09-27');
    expect(utcDayOf('2026-09-28T00:00:00.000+00:00')).toBe('2026-09-28');
    expect(utcDayOf(null)).toBeNull();
  });

  it('says a duration only between two recorded times, rounded to the second', () => {
    // Run m6ed2dd9: started 14:21:46.005Z, finished 14:24:40.114Z.
    expect(durationWords('2026-09-27T14:21:46.005Z', '2026-09-27T14:24:40.114Z')).toBe('2 min 54 s');
    expect(durationWords('2026-09-27T14:16:38.938Z', '2026-09-27T14:17:08.967Z')).toBe('30 s');
    expect(durationWords('2026-09-27T10:00:00Z', '2026-09-27T11:02:03Z')).toBe('1 h 2 min 3 s');
    expect(durationWords('2026-09-27T14:21:46.005Z', null)).toBeNull();
    expect(durationWords('2026-09-27T14:24:40Z', '2026-09-27T14:21:46Z')).toBeNull();
  });
});
