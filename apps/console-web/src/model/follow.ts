// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// AUTO-SCROLL THAT PAUSES (docs/autonomous-demo/console-redesign-2026-09-27.md, rule 6.4).
//
// Four events: entries appended, the list scrolled (at its end or not, within 24 px), the jump pill pressed, and the list
// or its content resized (a width change rewraps the lines). Following, new entries scroll into view and a resize keeps
// the list at its end. Scrolled away from the end, following pauses and the pill counts the entries that arrived since; a
// resize leaves a paused list where it is. Pressing the pill or scrolling back to the end resumes. The count only
// changes when entries arrive.

export interface FollowState {
  following: boolean;
  unseen: number;
}

export type FollowEvent =
  | { kind: 'appended'; count: number }
  | { kind: 'scrolled'; atEnd: boolean }
  | { kind: 'jump' }
  | { kind: 'resized' };

/** The next state, and whether the list is moved to its end for this event. */
export interface FollowAnswer {
  state: FollowState;
  scrollToEnd: boolean;
}

export const FOLLOWING: FollowState = { following: true, unseen: 0 };

/** How close to the end, in CSS pixels, still counts as at the end. */
export const AT_END_PX = 24;

export function followStep(state: FollowState, event: FollowEvent): FollowAnswer {
  switch (event.kind) {
    case 'appended':
      if (event.count <= 0) return { state, scrollToEnd: false };
      return state.following
        ? { state, scrollToEnd: true }
        : { state: { following: false, unseen: state.unseen + event.count }, scrollToEnd: false };
    case 'scrolled':
      return { state: event.atEnd ? FOLLOWING : { following: false, unseen: state.unseen }, scrollToEnd: false };
    case 'jump':
      return { state: FOLLOWING, scrollToEnd: true };
    case 'resized':
      return { state, scrollToEnd: state.following };
  }
}

export function follow(state: FollowState, event: FollowEvent): FollowState {
  return followStep(state, event).state;
}

export function isAtEnd(box: { scrollTop: number; clientHeight: number; scrollHeight: number }): boolean {
  return box.scrollHeight - (box.scrollTop + box.clientHeight) <= AT_END_PX;
}

export function unseenWords(count: number): string {
  return count === 1 ? '1 new entry below' : `${String(count)} new entries below`;
}
