// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The imported design's palette, copied from its own `renderVals()` (design lines 548-550) rather than sampled from a
// picture of it. These six colours and the two maps under them are what the design means by "ok", "waiting", "warn"
// and "bad", and every tone on the ported screen resolves through here so that no panel invents a seventh.

/** The design's six semantic colours, verbatim. */
export const C = {
  accent: '#00d4ff',
  wait: '#a78bfa',
  warn: '#fbbf24',
  bad: '#f87171',
  ok: '#34d399',
  mute: '#8b8b96',
} as const;

/** The greys the design uses for text, in the order it uses them: brightest first. */
export const INK = {
  bright: '#f4f4f5',
  body: '#e4e4e7',
  soft: '#d4d4d8',
  quiet: '#a1a1aa',
  faint: '#5b5b66',
  rule: '#3a3a44',
} as const;

/** The page and card backgrounds the design states outside the token sheet. */
export const SURFACE = {
  page: '#0a0a0f',
  well: '#07070b',
  raised: '#0d0d13',
  chip: '#13131a',
  sunken: '#101017',
} as const;

/**
 * THE TONE OF ONE LINE OF HISTORY.
 *
 * The design carries seven kinds (`sys`, `robot`, `cake`, `ok`, `wait`, `warn`, `bad`) with a dot colour and a text
 * colour each. This console's records are contract events, so what decides the tone is the record's own kind and its
 * own reason code; see `timeline.ts`, which is the only place that mapping is made.
 */
export type Tone = 'sys' | 'robot' | 'cake' | 'ok' | 'wait' | 'warn' | 'bad';

export const TONE_DOT: Record<Tone, string> = {
  sys: C.mute,
  robot: C.mute,
  cake: C.accent,
  ok: C.ok,
  wait: C.wait,
  warn: C.warn,
  bad: C.bad,
};

export const TONE_INK: Record<Tone, string> = {
  sys: INK.quiet,
  robot: INK.quiet,
  cake: INK.soft,
  ok: INK.soft,
  wait: C.wait,
  warn: C.warn,
  bad: C.bad,
};

/** A translucent fill of a semantic colour, the way the design writes its chips. */
export function wash(hex: string, alpha: number): string {
  const r = Number.parseInt(hex.slice(1, 3), 16);
  const g = Number.parseInt(hex.slice(3, 5), 16);
  const b = Number.parseInt(hex.slice(5, 7), 16);
  return `rgba(${String(r)},${String(g)},${String(b)},${String(alpha)})`;
}
