// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import type { ReactNode } from 'react';

export type BadgeTone = 'neutral' | 'ok' | 'warn' | 'bad' | 'info' | 'quiet' | 'live' | 'replay' | 'synthetic';

export function Badge(props: {
  tone?: BadgeTone;
  strong?: boolean;
  dot?: boolean;
  /** A glyph before the words, so a status is never carried by colour alone. */
  icon?: ReactNode;
  /** True when the badge carries an identifier or a code rather than words, which is the only thing set in mono. */
  code?: boolean;
  title?: string;
  children: ReactNode;
}): ReactNode {
  const tone = props.tone ?? 'neutral';
  const classes = ['badge', tone === 'neutral' ? '' : `badge--${tone}`, props.strong === true ? 'badge--strong' : '', props.code === true ? 'badge--code' : ''];
  return (
    <span className={classes.filter((piece) => piece !== '').join(' ')} title={props.title}>
      {props.dot === true ? <span className="dot" /> : null}
      {props.icon ?? null}
      {props.children}
    </span>
  );
}

/** ok / degraded / failed / unknown, the way every panel on this screen shows health. */
export function healthTone(health: string): BadgeTone {
  if (health === 'ok') return 'ok';
  if (health === 'degraded') return 'warn';
  if (health === 'failed') return 'bad';
  return 'quiet';
}

/** true / false / not determined, where a null is never drawn as a false. */
export function Tristate(props: { value: boolean | null; trueLabel?: string; falseLabel?: string }): ReactNode {
  if (props.value === null) return <Badge tone="quiet">not determined</Badge>;
  return props.value ? <Badge tone="ok">{props.trueLabel ?? 'true'}</Badge> : <Badge tone="bad">{props.falseLabel ?? 'false'}</Badge>;
}
