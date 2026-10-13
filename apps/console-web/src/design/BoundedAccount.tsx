// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// A CARD'S LONG ACCOUNT IN A BOUNDED REGION (docs/autonomous-demo/console-redesign-2026-09-27.md, section 2.1; operator
// layout ruling 2026-09-28, "stable positions").
//
// The mission card and Cake keep one footprint whether a run is idle, running or ended, so the stages, the scene and the
// wrist camera under them never move. Whatever a card has to say beyond that footprint is not dropped: it is drawn whole
// inside this region, which scrolls within the card, can be reached by keyboard (it is a focusable, labelled region),
// and says in words when more of it is below the fold, so nothing reads as the end of the account while it is not.
//
// FOLLOWING THE NEWEST RECORD (verify-r5 M2): Cake's records are oldest first, and the region never scrolled itself, so
// the result of the activation the operator had just pressed was the first card to go below the fold. A region given
// `follow` (the newest record's id, set on that record as `data-follow-target`) scrolls itself, never the page, so that
// record's top is at the region's top whenever `follow` changes; the older records are above it, and the region says so.

import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { INK } from './theme';

export interface BoundedAccountProps {
  /** What the region holds, for its accessible name: "the mission's account", "Cake's records". */
  label: string;
  /** `data-bounded` on the region, so a probe can find it. */
  name: string;
  children: ReactNode;
  /** The id of the record to keep in view (its element carries `data-follow-target` with it), or null for none. */
  follow?: string | null;
}

/** Whether a scrolled box has more below what it shows now. Two pixels of slack for sub-pixel layout. */
export function moreBelow(box: { scrollTop: number; clientHeight: number; scrollHeight: number }): boolean {
  return box.scrollTop + box.clientHeight < box.scrollHeight - 2;
}

/**
 * The region's scrollTop that puts a record's top at the region's top, clamped to what the region can scroll. `top` is
 * the region's top in the view, and `target` the record's top in the view, both read with the region's current scroll.
 */
export function followTop(region: { top: number; scrollTop: number; clientHeight: number; scrollHeight: number }, target: number): number {
  const wanted = region.scrollTop + (target - region.top);
  return Math.max(0, Math.min(wanted, region.scrollHeight - region.clientHeight));
}

export function BoundedAccount(props: BoundedAccountProps): ReactNode {
  const ref = useRef<HTMLDivElement | null>(null);
  const [more, setMore] = useState(false);
  const [above, setAbove] = useState(false);
  const measure = useCallback(() => {
    const box = ref.current;
    if (box === null) return;
    setMore(moreBelow(box));
    setAbove(box.scrollTop > 2);
  }, []);
  const follow = props.follow ?? null;
  useEffect(() => {
    const box = ref.current;
    if (box === null || follow === null) return;
    const target = Array.from(box.querySelectorAll<HTMLElement>('[data-follow-target]')).find((element) => element.getAttribute('data-follow-target') === follow);
    if (target === undefined) return;
    const region = box.getBoundingClientRect();
    box.scrollTop = followTop({ top: region.top, scrollTop: box.scrollTop, clientHeight: box.clientHeight, scrollHeight: box.scrollHeight }, target.getBoundingClientRect().top);
    measure();
  }, [follow, measure]);
  useEffect(() => {
    const box = ref.current;
    measure();
    if (box === null || typeof ResizeObserver === 'undefined') return;
    // THE CONTENT CHANGES WITHOUT A SCROLL (a run ends, the account arrives): watch the region and what it holds.
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    for (const child of Array.from(box.children)) observer.observe(child);
    return () => observer.disconnect();
  });
  return (
    <>
      <div
        ref={ref}
        role="region"
        aria-label={props.label}
        tabIndex={0}
        data-bounded={props.name}
        data-follow={follow ?? undefined}
        onScroll={measure}
        style={{ flex: '1 1 auto', minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', display: 'flex', flexDirection: 'column', gap: 10, paddingRight: 6, scrollbarGutter: 'stable', scrollbarColor: '#52525b transparent', scrollbarWidth: 'thin' }}
      >
        {props.children}
      </div>
      {more || above ? (
        <div
          data-bounded-more={more ? props.name : undefined}
          data-bounded-above={above ? props.name : undefined}
          aria-hidden="true"
          style={{ flex: 'none', fontSize: 12, color: INK.quiet, borderTop: '1px dashed var(--color-border)', paddingTop: 4 }}
        >
          {more && above ? 'More above and below: scroll within this card.' : more ? 'More below: scroll within this card.' : 'More above: scroll within this card.'}
        </div>
      ) : null}
    </>
  );
}
