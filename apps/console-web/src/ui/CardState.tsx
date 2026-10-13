// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The eight states every card on this screen can be in, and the one set of words for them.
//
// This exists because each panel was choosing its own word for the same condition, and the words were not the same
// fact. A card that prints "unavailable" seven times during a run says the runtime tried and could not report seven
// things; what was true was that seven things had not happened yet. The two are different, and an operator who is
// told the first one when the second is true is being told the runtime is broken.
//
//   IDLE      nothing has been asked of it. No run, no command, nothing to report.
//   WAITING   it has been asked for and has not happened yet. This is "not yet", never "unavailable".
//   ACTIVE    it is happening now.
//   BLOCKED   something else must change first, and the runtime named what.
//   REFUSED   it was asked for and refused, and the refusal has a reason.
//   STALE     there is a value and it is too old to be read as current. The age is shown beside it.
//   UNKNOWN   the runtime was in a position to report it and could not. This is the one that says "unavailable".
//   FAILED    it was attempted and it failed.
//
// A card takes the state and the words together: `CARD_STATE_WORDS` is the default, and a card whose subject has a
// more exact phrase (an unevaluated check reads "Not checked yet") passes that phrase instead of inventing one at the
// point of render.

import type { ReactNode } from 'react';
import { predicateState } from '../model/predicate';
import type { PredicateView } from '../model/types';
import { Badge } from './Badge';
import type { BadgeTone } from './Badge';
import { IconAlert, IconBlocked, IconClock, IconUnknown } from './Icons';

export type CardState = 'idle' | 'waiting' | 'active' | 'blocked' | 'refused' | 'stale' | 'unknown' | 'failed';

export const CARD_STATES: readonly CardState[] = ['idle', 'waiting', 'active', 'blocked', 'refused', 'stale', 'unknown', 'failed'];

export const CARD_STATE_WORDS: Record<CardState, string> = {
  idle: 'Not started',
  waiting: 'Not yet',
  active: 'In progress',
  blocked: 'Blocked',
  refused: 'Refused',
  stale: 'Stale',
  unknown: 'Unavailable',
  failed: 'Failed',
};

/** What an evaluation that has not been made reads as. It is a waiting card and it is never the word unavailable. */
export const NOT_CHECKED_YET = 'Not checked yet';

export function cardStateTone(state: CardState): BadgeTone {
  if (state === 'active') return 'info';
  if (state === 'failed') return 'bad';
  if (state === 'blocked' || state === 'refused' || state === 'stale') return 'warn';
  return 'quiet';
}

/** Status is never carried by colour alone: every state has a glyph, and the active one has the moving dot. */
function cardStateIcon(state: CardState): ReactNode {
  if (state === 'failed') return <IconAlert />;
  if (state === 'blocked' || state === 'refused') return <IconBlocked />;
  if (state === 'waiting' || state === 'stale') return <IconClock />;
  if (state === 'unknown' || state === 'idle') return <IconUnknown />;
  return null;
}

export interface CardStatusProps {
  state: CardState;
  /** The exact phrase for this card's subject. The shared word for the state is used when none is given. */
  words?: string;
  /** Why it is in this state: the runtime's reason, shown on hover and never instead of the words. */
  title?: string;
}

/** The one badge that says what state a card is in, in the one set of words. */
export function CardStatus(props: CardStatusProps): ReactNode {
  return (
    <Badge tone={cardStateTone(props.state)} icon={cardStateIcon(props.state)} dot={props.state === 'active'} title={props.title}>
      {props.words ?? CARD_STATE_WORDS[props.state]}
    </Badge>
  );
}

/**
 * The card state of a recorded evaluation, or null when the record carries a VERDICT.
 *
 * A verdict is not a card state: true and false are the answer the check came back with, and they are drawn as the
 * answer. This is only for the four ways a check can have no answer, and each of them gets its own words rather than
 * one word for all four.
 */
export function cardStateOfPredicate(predicate: Pick<PredicateView, 'state' | 'value'>): { state: CardState; words: string } | null {
  const recorded = predicateState(predicate);
  if (recorded === 'not_evaluated') return { state: 'waiting', words: NOT_CHECKED_YET };
  if (recorded === 'not_applicable') return { state: 'idle', words: 'Does not apply here' };
  if (recorded === 'unavailable') return { state: 'unknown', words: CARD_STATE_WORDS.unknown };
  if (predicate.value === null) return { state: 'waiting', words: NOT_CHECKED_YET };
  return null;
}
