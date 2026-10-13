// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The two primitives every card on this screen is built out of: the age that has to keep moving, and the one set of
// words for the state a card is in. Both were being written out panel by panel, and the words were not the same.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { PredicateView } from '../model/types';
import { Age, ageWords } from './Age';
import { CARD_STATES, CARD_STATE_WORDS, CardStatus, NOT_CHECKED_YET, cardStateOfPredicate, cardStateTone } from './CardState';
import { Panel } from './Panel';

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

function predicate(extra: Partial<PredicateView> = {}): PredicateView {
  return {
    predicate: 'object_secured',
    value: null,
    state: null,
    reason_code: null,
    rule_ref: null,
    rule_digest: null,
    threshold: null,
    measured: null,
    unit: null,
    evidence: [],
    consumed_source_stamp: null,
    measurements: [],
    seq: 5,
    t_mono_ns: 5_000_000,
    clock_domain: 'laptop_monotonic',
    ...extra,
  };
}

describe('an age in words', () => {
  it('is milliseconds while it is fresh and seconds once it is not', () => {
    expect(ageWords(412)).toBe('412 ms');
    expect(ageWords(9_999)).toBe('9999 ms');
    expect(ageWords(12_400)).toBe('12.4 s');
    expect(ageWords(95_000)).toBe('1 min 35 s');
  });

  it('shows a stamp that is ahead of the clock as no age at all, never as a negative number', () => {
    expect(ageWords(-4_000)).toBe('0 ms');
  });
});

describe('the age that a panel draws', () => {
  it('advances with the reading it is given, which is what the shared tick moves', () => {
    const at = 1_000_000;
    const early = text(renderToStaticMarkup(createElement(Age, { atMs: at, nowMs: at + 400 })));
    const later = text(renderToStaticMarkup(createElement(Age, { atMs: at, nowMs: at + 3_400 })));
    expect(early).toBe('400 ms ago');
    expect(later).toBe('3400 ms ago');
  });

  it('says nothing has arrived rather than printing an age of zero or the word unavailable', () => {
    const words = text(renderToStaticMarkup(createElement(Age, { atMs: null, nowMs: 5 })));
    expect(words).toBe('nothing has arrived yet');
    expect(words).not.toContain('unavailable');
  });

  it('takes the absent words from the caller, because what is missing differs from card to card', () => {
    const markup = renderToStaticMarkup(createElement(Age, { atMs: null, nowMs: 5, absent: 'no line has reached this tab' }));
    expect(text(markup)).toBe('no line has reached this tab');
  });

  it('marks an age past the threshold as stale and states the threshold it used', () => {
    const markup = renderToStaticMarkup(createElement(Age, { atMs: 0, nowMs: 5_000, staleAfterMs: 1_500 }));
    expect(markup).toContain('age--stale');
    expect(markup).toContain('counted as stale after 1500 ms');
    expect(renderToStaticMarkup(createElement(Age, { atMs: 0, nowMs: 900, staleAfterMs: 1_500 }))).not.toContain('age--stale');
  });
});

describe('the state a card is in', () => {
  it('has one word for each of the eight, and never the word unavailable for something that has not happened', () => {
    for (const state of CARD_STATES) {
      const words = text(renderToStaticMarkup(createElement(CardStatus, { state })));
      expect(words, state).toBe(CARD_STATE_WORDS[state]);
    }
    expect(CARD_STATE_WORDS.waiting).toBe('Not yet');
    expect(CARD_STATE_WORDS.unknown).toBe('Unavailable');
  });

  it('carries the state in a glyph as well as in the colour', () => {
    for (const state of CARD_STATES) {
      const markup = renderToStaticMarkup(createElement(CardStatus, { state }));
      expect(markup, state).toMatch(/<svg|class="dot"/);
    }
  });

  it('takes the exact phrase from the card when the shared word is not the right one', () => {
    const markup = renderToStaticMarkup(createElement(CardStatus, { state: 'waiting', words: NOT_CHECKED_YET }));
    expect(text(markup)).toBe('Not checked yet');
  });

  it('keeps a failure loud and an absence quiet', () => {
    expect(cardStateTone('failed')).toBe('bad');
    expect(cardStateTone('active')).toBe('info');
    expect(cardStateTone('stale')).toBe('warn');
    expect(cardStateTone('waiting')).toBe('quiet');
  });

  it('shows the state on the card head when the card states one', () => {
    const markup = renderToStaticMarkup(
      createElement(Panel, { title: 'verification', state: 'waiting', stateWords: NOT_CHECKED_YET, children: 'body' }),
    );
    expect(text(markup)).toContain('Not checked yet');
    expect(text(renderToStaticMarkup(createElement(Panel, { title: 'verification', children: 'body' })))).not.toContain('Not checked yet');
  });
});

describe('the state of a recorded evaluation', () => {
  it('reads an evaluation nobody made as not checked yet, in either way a record says it', () => {
    expect(cardStateOfPredicate(predicate({ state: 'not_evaluated' }))?.words).toBe(NOT_CHECKED_YET);
    expect(cardStateOfPredicate(predicate({ state: null, value: null }))?.words).toBe(NOT_CHECKED_YET);
    expect(cardStateOfPredicate(predicate({ state: 'not_evaluated' }))?.state).toBe('waiting');
  });

  it('keeps the four ways of having no answer apart instead of printing one word for all of them', () => {
    expect(cardStateOfPredicate(predicate({ state: 'not_applicable' }))).toEqual({ state: 'idle', words: 'Does not apply here' });
    expect(cardStateOfPredicate(predicate({ state: 'unavailable' }))).toEqual({ state: 'unknown', words: 'Unavailable' });
  });

  it('returns nothing for a record that carries a verdict, because a verdict is drawn as the answer it is', () => {
    expect(cardStateOfPredicate(predicate({ value: true }))).toBeNull();
    expect(cardStateOfPredicate(predicate({ value: false }))).toBeNull();
    expect(cardStateOfPredicate(predicate({ state: 'evaluated', value: false }))).toBeNull();
  });
});
