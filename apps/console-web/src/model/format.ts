// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Formatting. One rule runs through all of it: a value that was not recorded prints the word unavailable. It never
// prints a zero, a dash that could be read as a number, or a plausible default.

export const UNAVAILABLE = 'unavailable';

export function millis(value: number | null, digits = 0): string {
  return value === null || !Number.isFinite(value) ? UNAVAILABLE : `${value.toFixed(digits)} ms`;
}

export function nanosAsMillis(value: number | null, digits = 1): string {
  return value === null || !Number.isFinite(value) ? UNAVAILABLE : `${(value / 1e6).toFixed(digits)} ms`;
}

export function number(value: number | null, digits = 1, unit?: string): string {
  if (value === null || !Number.isFinite(value)) return UNAVAILABLE;
  return `${value.toFixed(digits)}${unit === undefined ? '' : ` ${unit}`}`;
}

export function integer(value: number | null): string {
  return value === null || !Number.isFinite(value) ? UNAVAILABLE : String(Math.round(value));
}

export function textOr(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? UNAVAILABLE : value;
}

export function tristate(value: boolean | null): string {
  return value === null ? 'not determined' : value ? 'true' : 'false';
}

/** The first 12 characters of a digest, which is how a digest is shown on this screen. */
export function digestPrefix(value: string | null): string {
  if (value === null) return UNAVAILABLE;
  const bare = value.startsWith('sha256:') ? value.slice('sha256:'.length) : value;
  return bare.slice(0, 12);
}

/** Freshness in words. The threshold is a display choice and is stated on the screen next to it. */
export type Freshness = 'fresh' | 'stale' | 'unavailable';

export function freshness(ageMs: number | null, staleAfterMs: number): Freshness {
  if (ageMs === null || !Number.isFinite(ageMs)) return 'unavailable';
  return ageMs > staleAfterMs ? 'stale' : 'fresh';
}

export function seconds(value: number | null, digits = 2): string {
  return value === null || !Number.isFinite(value) ? UNAVAILABLE : `${value.toFixed(digits)} s`;
}
