// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE TIMELINE, full width (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 2.1, 6.3 and 6.4).
//
// The store's append-only list (`model/missionTimeline.ts`), oldest at the top, each entry keyed and timed in UTC, each
// mission run a group under its heading and closed by its word. New entries appear without animation. The list follows
// its end until the operator scrolls away; then a pill counts what arrived since, and pressing it or scrolling back to
// the end resumes (`model/follow.ts`).

import type { ReactNode, Ref, UIEvent } from 'react';
import type { FollowState } from '../model/follow';
import { unseenWords } from '../model/follow';
import type { TimelineEntry } from '../model/missionTimeline';
import { C, INK, SURFACE, wash } from './theme';
import { TONE_COLOUR } from './TopBar';

export interface TimelineRegionProps {
  entries: readonly TimelineEntry[];
  follow: FollowState;
  onJump: () => void;
  onScroll: (event: UIEvent<HTMLOListElement>) => void;
  listRef: Ref<HTMLOListElement> | null;
  notReadSince: string | null;
  /**
   * The code of a mission read that failed before any mission was read, or null. The timeline is built from the mission,
   * so with it unread the list says that, never that nothing was recorded (status 2026-09-27, LOW 4).
   */
  missionUnread?: string | null;
  /**
   * True while this display has never had an answer from the runtime (verify-r1 must_fix 4): it says so, with no time,
   * in place of a "Not read since" it has no read for, and the timeline carries no "lost" entry for it.
   */
  notAnswered?: boolean;
}

const KIND_COLOUR: Record<TimelineEntry['kind'], string> = {
  mission: INK.bright,
  cake: C.accent,
  retry: C.warn,
  run: INK.bright,
  event: INK.body,
  press: INK.soft,
  link: C.warn,
};

function Entry(props: { entry: TimelineEntry }): ReactNode {
  const { entry } = props;
  const grouped = entry.group !== null && entry.kind !== 'run';
  const colour = entry.verdict === null ? KIND_COLOUR[entry.kind] : TONE_COLOUR[entry.verdict.tone];
  return (
    <li
      data-timeline-entry={entry.kind}
      data-key={entry.key}
      data-group={entry.group ?? undefined}
      data-t={entry.t ?? undefined}
      data-verdict={entry.verdict?.id}
      title={entry.t_title ?? entry.t ?? undefined}
      style={{
        listStyle: 'none',
        padding: entry.kind === 'run' ? '8px 10px 4px' : '3px 10px',
        marginLeft: grouped ? 18 : 0,
        borderLeft: grouped ? `2px solid ${INK.rule}` : 'none',
        fontSize: entry.kind === 'run' ? 13 : 13,
        fontWeight: entry.kind === 'run' || entry.verdict !== null ? 600 : 400,
        color: colour,
        lineHeight: 1.45,
        overflowWrap: 'anywhere',
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {entry.text}
      {entry.more.map((line) => (
        <div key={line} style={{ fontSize: 12, fontWeight: 400, color: INK.quiet }}>
          {line}
        </div>
      ))}
    </li>
  );
}

export function TimelineRegion(props: TimelineRegionProps): ReactNode {
  const paused = !props.follow.following;
  return (
    <section aria-label="Timeline" data-region="timeline" style={{ minWidth: 0, position: 'relative', borderTop: '1px solid var(--color-border)', paddingTop: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }}>
        <span style={{ fontSize: 13, color: INK.quiet }}>Timeline</span>
        <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>Oldest first. Times in UTC.</span>
        {props.notReadSince === null ? null : (
          <span data-stale-marker="timeline" style={{ fontSize: 12, color: C.warn }}>
            Not read since {props.notReadSince}
          </span>
        )}
        {props.notAnswered !== true ? null : (
          <span data-stale-marker="timeline" data-not-answered="" style={{ fontSize: 12, color: C.warn }}>
            The runtime has not answered this display.
          </span>
        )}
      </div>
      <ol
        ref={props.listRef ?? undefined}
        role="log"
        aria-live="polite"
        data-follow={paused ? 'paused' : 'following'}
        onScroll={props.onScroll}
        className="demo-timeline"
        style={{ margin: 0, padding: '4px 0', height: '38vh', minHeight: 260, overflowY: 'auto', background: SURFACE.well, borderRadius: 8 }}
      >
        {(props.missionUnread ?? null) === null ? null : (
          <li data-mission-unread="" style={{ listStyle: 'none', padding: '6px 10px', fontSize: 13, color: C.warn }}>
            The mission has not been read: {props.missionUnread}.
          </li>
        )}
        {props.entries.length === 0 ? (
          (props.missionUnread ?? null) !== null ? null : (
            <li style={{ listStyle: 'none', padding: '6px 10px', fontSize: 13, color: INK.quiet }}>Nothing recorded on this display yet.</li>
          )
        ) : (
          props.entries.map((entry) => <Entry key={entry.key} entry={entry} />)
        )}
      </ol>
      {paused && props.follow.unseen > 0 ? (
        <button
          type="button"
          data-control="timeline-jump"
          data-unseen={String(props.follow.unseen)}
          onClick={props.onJump}
          style={{ position: 'absolute', left: '50%', bottom: 10, transform: 'translateX(-50%)', font: '600 12px var(--font-sans), sans-serif', color: C.accent, background: SURFACE.raised, border: `1px solid ${wash(C.accent, 0.5)}`, borderRadius: 999, padding: '4px 12px', cursor: 'pointer' }}
        >
          {unseenWords(props.follow.unseen)}
        </button>
      ) : null}
    </section>
  );
}
