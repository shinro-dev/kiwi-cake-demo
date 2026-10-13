// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ONE DESIGN-SYSTEM COMPONENT THE IMPORTED DESIGN USES, implemented locally.
//
// `Cake Live Demo v2.dc.html` reaches for `ShinroStudio.Button` eight times (template lines 44, 45, 46, 184, 253, 255,
// 310, 311) and for nothing else out of the 744-component bundle. Vendoring that bundle for eight buttons would pull a
// React dependency graph and a 267.7 KB stylesheet into this application to obtain four variants and one size, and the
// stylesheet is the file the design integration could not return whole (see design/IMPORT.md). So the geometry is
// reproduced here instead, from the design's own evidence, and the fact that it is a reproduction is written down
// rather than implied.
//
// WHAT THE GEOMETRY RESTS ON. The design states the rendered box of every button it places: `hint-size="76px,32px"` on
// the header trio, `150px,32px` on the two Cake actions, `120px,32px` on the receipt, `70px,32px` on the walkthrough
// pair. Every one of them is 32 px high, which is what `size="sm"` resolves to, and that is the measurement this
// implementation matches. The variants are the four the design names: `default`, `secondary`, `outline` and `ghost`.
// The colours are the design's own palette, taken from the same file rather than guessed at from a screenshot.
//
// WHAT IS NOT NEGOTIABLE, whatever the styling does:
//
//   IT IS A `<button>`. Not a div with a click handler. It submits nothing (`type="button"`), it is reachable by Tab,
//   it fires on Enter and on Space, and a screen reader calls it a button, because all of that is the element's and
//   none of it is this file's to re-implement.
//   `disabled` MEANS DISABLED. The attribute is set, so the browser refuses the click, refuses the keypress and takes
//   the control out of the tab order. A disabled look without the attribute is how an operator ends up commanding a
//   robot through a button that was drawn as off, and this console has one control that starts a physical machine.
//   FOCUS STAYS VISIBLE. `:focus-visible` draws a ring in every variant, including `ghost`, which has no border of its
//   own to thicken. Keyboard operation of this screen is a requirement and not a preference.

import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'default' | 'secondary' | 'outline' | 'ghost';
export type ButtonSize = 'sm';

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

/**
 * The imported design's button.
 *
 * `size` exists as a prop because the design writes it, and it takes the one value the design ever passes. It is not
 * widened to sizes nothing asks for: an unused variant is an untested variant, and this file is small on purpose.
 */
export function Button({ variant = 'default', size = 'sm', children, ...rest }: ButtonProps): ReactNode {
  // THE DEFAULT VARIANT IS THE PRIMARY CONTROL, and it says so in `data-primary` (console-redesign-2026-09-27.md, rule
  // 4.3): a screen draws at most one, and a test reads it from the markup.
  return (
    <button type="button" className={`dsbutton dsbutton--${variant} dsbutton--${size}`} data-primary={variant === 'default' ? 'true' : undefined} {...rest}>
      {children}
    </button>
  );
}
