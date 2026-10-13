// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Icons are drawn here as SVG paths. There are no image files in this application and no icon font: a glyph from a
// font is a character, and this screen carries none outside plain Latin text and digits.

import type { ReactNode } from 'react';

function Svg(props: { children: ReactNode; size?: number; title: string }): ReactNode {
  const size = props.size ?? 14;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label={props.title}
      style={{ flex: 'none', verticalAlign: '-0.15em' }}
    >
      {props.children}
    </svg>
  );
}

export function IconTrue(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="true">
      <path d="M3 8.5 6.4 12 13 4.5" />
    </Svg>
  );
}

export function IconFalse(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="false">
      <path d="M4 4l8 8M12 4l-8 8" />
    </Svg>
  );
}

export function IconUnknown(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="not determined">
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 10.5v.01M6.3 6.2a1.8 1.8 0 1 1 1.9 2.2v.6" />
    </Svg>
  );
}

export function IconCamera(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="camera">
      <path d="M1.8 5.2h2.6l1-1.6h5.2l1 1.6h2.6v7.2H1.8z" />
      <circle cx="8" cy="8.6" r="2.3" />
    </Svg>
  );
}

export function IconFrame(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="decision frame">
      <rect x="2" y="3" width="12" height="10" rx="1" />
      <path d="M5.5 3v10M2 6.5h12" />
    </Svg>
  );
}

export function IconPad(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="pad">
      <path d="M2.5 11.5 5 4.5h6l2.5 7z" />
      <path d="M6.6 8h2.8" />
    </Svg>
  );
}

export function IconStop(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="stop">
      <rect x="3" y="3" width="10" height="10" rx="1.4" />
    </Svg>
  );
}

export function IconLink(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="link">
      <path d="M6.6 9.4a2.6 2.6 0 0 1 0-3.7l1.8-1.8a2.6 2.6 0 0 1 3.7 3.7l-.9.9" />
      <path d="M9.4 6.6a2.6 2.6 0 0 1 0 3.7l-1.8 1.8a2.6 2.6 0 0 1-3.7-3.7l.9-.9" />
    </Svg>
  );
}

export function IconClock(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="timing">
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 4.8V8l2.2 1.6" />
    </Svg>
  );
}

export function IconLayers(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="layers">
      <path d="M8 2.6 14 6l-6 3.4L2 6z" />
      <path d="M2.6 9 8 12l5.4-3" />
    </Svg>
  );
}

export function IconBlocked(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="blocked">
      <circle cx="8" cy="8" r="5.6" />
      <path d="M4.4 4.4l7.2 7.2" />
    </Svg>
  );
}

export function IconAlert(props: { size?: number }): ReactNode {
  return (
    <Svg size={props.size} title="attention">
      <path d="M8 2.6 14.4 13H1.6z" />
      <path d="M8 6.6v3M8 11.2v.01" />
    </Svg>
  );
}
