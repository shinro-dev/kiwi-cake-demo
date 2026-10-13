// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import type { ReactNode } from 'react';
import { UNAVAILABLE } from '../model/format';
import { CardStatus } from './CardState';
import type { CardState } from './CardState';

export function Panel(props: {
  title: string;
  /**
   * Which of the eight states this card is in. Optional: a panel that says nothing here draws no status badge, which
   * is what every panel written before the shared vocabulary existed does.
   */
  state?: CardState;
  /** The exact phrase for this card's subject, when the shared word for the state is not the right one. */
  stateWords?: string;
  extra?: ReactNode;
  note?: ReactNode;
  flush?: boolean;
  className?: string;
  children: ReactNode;
}): ReactNode {
  return (
    <section className={`panel ${props.className ?? ''}`}>
      <header className="panel__head">
        <h2 className="panel__title">{props.title}</h2>
        {props.state === undefined ? null : (
          <div className="panel__head-state">
            <CardStatus state={props.state} words={props.stateWords} />
          </div>
        )}
        {props.extra === undefined ? null : <div className="panel__head-extra">{props.extra}</div>}
      </header>
      <div className={`panel__body ${props.flush === true ? 'panel__body--flush' : ''}`}>{props.children}</div>
      {props.note === undefined ? null : <footer className="panel__note">{props.note}</footer>}
    </section>
  );
}

/** A label and a value. A value that equals the word unavailable is styled as absent, never as data. */
export function Field(props: { label: string; children: ReactNode }): ReactNode {
  const absent = props.children === UNAVAILABLE || props.children === null || props.children === undefined;
  return (
    <>
      <div className="field__label">{props.label}</div>
      <div className={`field__value ${absent ? 'field__value--unavailable' : ''}`}>{absent ? UNAVAILABLE : props.children}</div>
    </>
  );
}

/**
 * A block of label-and-value pairs.
 *
 * `pairs` lays TWO pairs to a row instead of one, for a short block of short values in a wide column. It is a height
 * decision and nothing else: four fields one to a row cost about 110 px of a 900 px screen with half of every row
 * empty to the right of the value, and the same four in two rows cost about 55 px. It is not for long values, which
 * would wrap inside a half-width cell, and it falls back to one pair a row in a narrow window.
 */
export function Fields(props: { wide?: boolean; pairs?: boolean; children: ReactNode }): ReactNode {
  const shape = props.pairs === true ? 'fields--pairs' : props.wide === true ? 'fields--wide' : '';
  return <div className={`fields ${shape}`}>{props.children}</div>;
}
