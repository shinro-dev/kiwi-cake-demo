// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// EVERY TIME THE DEMO SCREEN PRINTS (docs/autonomous-demo/console-redesign-2026-09-27.md, section 5.4).
//
// Absolute UTC, always: `HH:MM:SSZ` on the reference day (the mission's UTC start date, or today's with no mission),
// `YYYY-MM-DD HH:MM:SSZ` on any other. The element's title and its `data-t` carry the full ISO string with
// milliseconds. The runtime writes fractional seconds of more than one length (`.010Z` and `.070538Z` in the same run),
// so the parse reads any length and truncates to milliseconds. A duration is said only between two recorded times,
// never against this display's clock: nothing printed here changes while nothing new is recorded.

const UTC_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]00:?00)$/;

/** Milliseconds since the epoch of a UTC time as the runtime writes it, truncated to milliseconds; null otherwise. */
export function parseUtcMs(text: string | null | undefined): number | null {
  if (typeof text !== 'string') return null;
  const match = UTC_PATTERN.exec(text.trim());
  if (match === null) return null;
  const [, year, month, day, hour, minute, second, fraction] = match;
  const ms = fraction === undefined ? 0 : Number(fraction.slice(0, 3).padEnd(3, '0'));
  const value = Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second), ms);
  return Number.isFinite(value) ? value : null;
}

/** The UTC day (`YYYY-MM-DD`) of a recorded time, or null when it is not one. */
export function utcDayOf(text: string | null | undefined): string | null {
  const ms = parseUtcMs(text);
  return ms === null ? null : new Date(ms).toISOString().slice(0, 10);
}

/** A recorded time as the screen prints it: the words, the ISO string for `data-t`, and the title. */
export interface UtcStamp {
  text: string;
  iso: string;
  title: string;
}

/** The stamp of a recorded time against the reference day, or null when the value is not a UTC time. */
export function utcStamp(text: string | null | undefined, referenceDay: string | null): UtcStamp | null {
  const ms = parseUtcMs(text);
  if (ms === null) return null;
  const iso = new Date(ms).toISOString();
  const day = iso.slice(0, 10);
  const clock = `${iso.slice(11, 19)}Z`;
  return { text: day === referenceDay ? clock : `${day} ${clock}`, iso, title: iso };
}

/** The time between two recorded times, rounded to the second ("2 min 54 s"); null unless both are recorded and ordered. */
export function durationWords(fromText: string | null | undefined, toText: string | null | undefined): string | null {
  const from = parseUtcMs(fromText);
  const to = parseUtcMs(toText);
  if (from === null || to === null || to < from) return null;
  const total = Math.round((to - from) / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${String(hours)} h ${String(minutes)} min ${String(seconds)} s`;
  if (minutes > 0) return `${String(minutes)} min ${String(seconds)} s`;
  return `${String(seconds)} s`;
}
