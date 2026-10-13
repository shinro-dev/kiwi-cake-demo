// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// How long ago something reached THIS computer, in words, advancing on its own.
//
// Every age on the screen goes through here, for two reasons. The first is that an age has to keep moving: read off
// the wall clock while a panel renders, it stands still between the polls and then jumps, which reads as a number that
// was just measured. The shared tick behind `useNowMs` is what makes it advance. The second is that a timestamp this
// console never received is not an age of zero and not the word unavailable either: it is "nothing has arrived yet",
// and the caller says so in its own words when the default does not fit.
//
// The stamp this takes is always the BROWSER's own clock, taken when a line arrived here. Ages measured at the runtime
// and stamps taken on the robot's computer are other clocks on other machines; they are shown as the runtime reported
// them and are never subtracted from this one.

import type { ReactNode } from 'react';
import { freshness, millis, seconds } from '../model/format';
import { useNowMs } from '../store/useConsole';

/**
 * An age in words, at the precision the number deserves: milliseconds while it is fresh, seconds to a tenth up to a
 * minute and a half, and minutes and seconds after that.
 *
 * A negative age means the stamp is ahead of the reading, which happens when the clock is adjusted mid-session. It is
 * shown as zero rather than as a negative number, the same way the decision age refuses to go below zero.
 */
export function ageWords(ageMs: number): string {
  const age = Math.max(0, ageMs);
  if (age < 10_000) return millis(age);
  if (age < 90_000) return seconds(age / 1000, 1);
  const minutes = Math.floor(age / 60_000);
  const rest = Math.round((age - minutes * 60_000) / 1000);
  return `${String(minutes)} min ${String(rest)} s`;
}

/**
 * How long ago `atMs` was, on the shared reading of this computer's clock. Null when nothing was ever stamped.
 *
 * `nowMs` overrides the shared reading, which is how a test pins what a panel printed without depending on the clock.
 */
export function useAgeMs(atMs: number | null, nowMs?: number): number | null {
  const shared = useNowMs();
  if (atMs === null) return null;
  return (nowMs ?? shared) - atMs;
}

export interface AgeProps {
  /** When this arrived, in the browser's clock. Null when nothing has arrived. */
  atMs: number | null;
  /** The reading to measure against, for a test. The shared tick is used when it is not given. */
  nowMs?: number;
  /** Older than this counts as stale. The caller states the threshold on the screen next to it. */
  staleAfterMs?: number;
  /** What to say when nothing has arrived. It is never the word unavailable, which means something else here. */
  absent?: string;
  /** Appended after the age, so a caller can write "received 412 ms ago" without building the sentence twice. */
  suffix?: string;
}

/** An age that advances: "412 ms ago", and the caller's own words when there is no stamp to measure from. */
export function Age(props: AgeProps): ReactNode {
  const ageMs = useAgeMs(props.atMs, props.nowMs);
  if (ageMs === null) {
    return <span className="age age--absent">{props.absent ?? 'nothing has arrived yet'}</span>;
  }
  const stale = props.staleAfterMs !== undefined && freshness(ageMs, props.staleAfterMs) === 'stale';
  return (
    <span
      className={`age ${stale ? 'age--stale' : ''}`}
      title={props.staleAfterMs === undefined ? undefined : `counted as stale after ${millis(props.staleAfterMs)}`}
    >
      {ageWords(ageMs)} {props.suffix ?? 'ago'}
    </span>
  );
}
