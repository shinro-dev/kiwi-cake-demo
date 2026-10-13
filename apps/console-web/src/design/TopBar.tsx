// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE TOP BAR (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 2.1, 3.1, 4.2, 4.3 and 5.2).
//
// shinro, the one mode mark, the robot and its connection, the motion permission, ONE readiness verdict with its
// sentence, then Start, the one Stop and Diagnostics. The verdict is `model/readiness.ts`'s, computed from Start's own
// off-reason, so the word and the button cannot disagree. Start is the primary control only when the verdict is Ready.
// Stop has its own fixed danger style in every state and is never primary. Nothing here counts time: a robot that has
// not been read lately is marked with the fixed time of its last good read.

import type { ReactNode } from 'react';
import type { ModeMark } from '../model/modeMark';
import type { Readiness } from '../model/readiness';
import type { ReadyItem } from '../model/readyChecklist';
import type { Tone } from '../model/verdict';
import { asSentence } from './actionCard';
import { Button } from './Button';
import { C, INK, SURFACE, wash } from './theme';

export const TONE_COLOUR: Record<Tone, string> = { ok: C.ok, bad: C.bad, warn: C.warn, wait: C.wait, accent: C.accent, mute: C.mute, ink: INK.bright };

export interface TopBarProps {
  mode: ModeMark;
  robot: { label: string; connection: string; tone: Tone; notReadSince: string | null };
  motion: { words: string; tone: Tone } | null;
  readiness: Readiness;
  start: { label: string; off: string | null; busy: boolean };
  /**
   * `stale`: the console server does not accept this tab's credential now (`model/reconnect.staleStop`). Stop stays in
   * place and enabled, with its title. A press calls `onStop`, whose one-time takeover sends Stop with the credential of
   * the page the server serves now (round 7 item 3); only when `stale.reload` (a takeover could not send) is it "Reload to
   * stop", and a press calls `onReload` instead.
   */
  stop: { off: string | null; busy: boolean; stale?: { reload: boolean; title: string } | null };
  onStart: () => void;
  onStop: () => void;
  onReload?: () => void;
  onDiagnostics: () => void;
  /** A control of the screen's own, drawn between Stop and Diagnostics (the show screen's Enable VLA). */
  extra?: ReactNode;
  /**
   * The bar's rendered height in whole px, now and whenever it changes (it wraps to about 250 px at 390 px, and grows
   * when its detail opens). The Diagnostics drawer starts below it, so the only Stop is never covered (verify-r7 M1).
   */
  onHeight?: (px: number) => void;
}

/**
 * Report `element`'s height, rounded up to a whole px, now and on every resize the observer sees. Where there is no
 * ResizeObserver it reports once. Returns the function that stops observing.
 */
export function observeHeight(element: HTMLElement, onHeight: (px: number) => void, Observer: typeof ResizeObserver | undefined): () => void {
  const report = (): void => onHeight(Math.ceil(element.getBoundingClientRect().height));
  report();
  if (Observer === undefined) return () => undefined;
  const observer = new Observer(report);
  observer.observe(element);
  return () => observer.disconnect();
}

// ONE CALLBACK REF PER LISTENER, so a re-render does not detach and re-observe the bar (a state setter is stable). A
// callback ref rather than an effect keeps TopBar free of hooks: its tests call it as a function to press its buttons.
const HEIGHT_REFS = new WeakMap<(px: number) => void, (element: HTMLElement | null) => (() => void) | undefined>();

export function heightRef(onHeight: (px: number) => void): (element: HTMLElement | null) => (() => void) | undefined {
  let ref = HEIGHT_REFS.get(onHeight);
  if (ref === undefined) {
    ref = (element) => (element === null ? undefined : observeHeight(element, onHeight, typeof ResizeObserver === 'undefined' ? undefined : ResizeObserver));
    HEIGHT_REFS.set(onHeight, ref);
  }
  return ref;
}

const MODE_TONE: Record<ModeMark['id'], string> = { live: C.ok, rehearsal: C.warn, recording: C.accent, not_recorded: C.mute };

/** One item of the readiness detail: its label and sentence, and beside it the hint of what to do about it, when it has one. */
function ReadyLine(props: { item: ReadyItem }): ReactNode {
  const item = props.item;
  return (
    <li data-ready-item={item.id} data-ready-state={item.state}>
      {item.label}: {item.sentence}
      {item.hint === null ? null : (
        <span data-ready-hint="" style={{ display: 'block', color: INK.soft }}>
          {item.hint}
        </span>
      )}
    </li>
  );
}

export function TopBar(props: TopBarProps): ReactNode {
  const verdict = props.readiness.verdict;
  const colour = TONE_COLOUR[verdict.tone];
  const startDisabled = props.start.off !== null || props.start.busy;
  const primary = props.readiness.startPrimary && !startDisabled;
  const stale = props.stop.stale ?? null;
  const reload = stale !== null && stale.reload ? stale.title : null;
  // A stale tab's Stop is on whatever the snapshot it can still read says: only a press in flight turns it off.
  const stopOff = stale === null ? props.stop.off : props.stop.busy && !stale.reload ? 'a stop request is in flight' : null;
  return (
    // STICKY (round 7 item 6): the only Stop lives here, and a press far down the page (an activation, Scan scene, Try
    // again, Return arm) left it hundreds of pixels above the view while the robot moved. The bar stays at the top of
    // the view over an opaque background, at every width. The Diagnostics drawer starts below it and one layer under it
    // (verify-r7 M1), so the drawer never covers this Stop.
    <header
      ref={props.onHeight === undefined ? undefined : heightRef(props.onHeight)}
      data-region="top-bar"
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: '8px 18px',
        minHeight: 58,
        padding: '8px 20px',
        borderBottom: '1px solid var(--color-border)',
        position: 'sticky',
        top: 0,
        zIndex: 20,
        background: SURFACE.page,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 'none' }}>
        <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-.02em' }}>shinro</span>
        <span
          data-mode-mark={props.mode.id}
          style={{ fontSize: 12, fontWeight: 600, color: MODE_TONE[props.mode.id], background: wash(MODE_TONE[props.mode.id], 0.1), border: `1px solid ${wash(MODE_TONE[props.mode.id], 0.35)}`, padding: '1px 8px', borderRadius: 6 }}
        >
          {props.mode.word}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', minWidth: 0 }}>
        <span data-robot-link="" style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, minWidth: 0, overflowWrap: 'anywhere' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', flex: 'none', background: TONE_COLOUR[props.robot.tone] }} />
          <span style={{ fontWeight: 600 }}>{props.robot.label}:</span>
          <span style={{ color: INK.soft }}>{props.robot.connection}</span>
          {props.robot.notReadSince === null ? null : (
            <span data-stale-marker="robot" style={{ fontSize: 12, color: C.warn }}>
              Not read since {props.robot.notReadSince}
            </span>
          )}
        </span>
        {props.motion === null ? null : (
          <span data-motion="" style={{ fontSize: 13, color: TONE_COLOUR[props.motion.tone] }}>
            {props.motion.words}
          </span>
        )}
      </div>

      <div style={{ flex: '1 1 260px', minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span data-readiness={verdict.id} data-verdict={verdict.id} style={{ fontSize: 15, fontWeight: 650, color: colour }}>
          {verdict.word}
        </span>
        {/* THE SENTENCE'S PLACE IS DRAWN IN EVERY STATE, ON ITS OWN ROW (verify-r5 M1). Ready has no sentence, and the bar
            was 58 px then against 113 px running at 1440, so every region under it moved 55 px when a run started from
            Ready, the normal state before Start. The place keeps its reserved lines (design.css) and is empty and hidden
            from assistive technology on Ready. */}
        <span
          id={props.readiness.sentence === null ? undefined : 'readiness-sentence'}
          data-readiness-sentence=""
          aria-hidden={props.readiness.sentence === null ? 'true' : undefined}
          style={{ flexBasis: '100%', fontSize: 13, color: INK.soft, lineHeight: 1.4, minWidth: 0, overflowWrap: 'anywhere' }}
        >
          {props.readiness.sentence}
        </span>
        {props.readiness.unchecked.length === 0 && props.readiness.checked.length === 0 ? null : (
          <details data-readiness-detail="" style={{ fontSize: 12, color: INK.quiet }}>
            <summary style={{ cursor: 'pointer' }}>What this rests on</summary>
            {/* THE LISTS SCROLL INSIDE A BOX BOUNDED BY THE VIEW (refix round 9, verify-r8 should_fix 2): they grew this
                sticky bar without bound, to 435 px in a 390 px view at 844 x 390, and pushed the only Stop out of it. */}
            <div data-readiness-scroll="" style={{ maxHeight: '30vh', overflowY: 'auto', overscrollBehavior: 'contain' }}>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.45 }}>
                {props.readiness.checked.map((item) => (
                  <ReadyLine key={item.id} item={item} />
                ))}
              </ul>
              {props.readiness.unchecked.length === 0 ? null : (
                <>
                  <div style={{ marginTop: 6, fontWeight: 600 }}>Not checked by the runtime</div>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18, lineHeight: 1.45 }}>
                    {props.readiness.unchecked.map((item) => (
                      <ReadyLine key={item.id} item={item} />
                    ))}
                  </ul>
                </>
              )}
            </div>
          </details>
        )}
      </div>

      {/* BELOW 1100 PX THESE TAKE A ROW OF THEIR OWN (design.css, data-topbar-controls): at 1039 px from Ready they fitted
          on the first row, and the running Start label, 18 px wider, wrapped them, so the bar grew 40 px at Start. */}
      <div data-topbar-controls="" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button
          variant={primary ? 'default' : 'secondary'}
          data-control="start"
          disabled={startDisabled}
          aria-disabled={startDisabled ? 'true' : 'false'}
          aria-describedby={props.readiness.sentence === null ? undefined : 'readiness-sentence'}
          onClick={props.onStart}
          title={props.start.off === null ? 'Start the mission: the runtime carries its targets one after the other.' : asSentence(props.start.off)}
        >
          {props.start.label}
        </Button>
        <Button
          variant="outline"
          data-control="stop"
          data-stop-reload={reload === null ? undefined : ''}
          data-stop-takeover={stale !== null && !stale.reload ? '' : undefined}
          disabled={stopOff !== null}
          aria-disabled={stopOff !== null ? 'true' : 'false'}
          onClick={reload === null ? props.onStop : props.onReload}
          title={stale !== null ? stale.title : stopOff === null ? 'Ask the runtime to stop the controller run and the mission. This is not an emergency stop.' : asSentence(stopOff)}
          style={{ color: C.bad, borderColor: wash(C.bad, 0.7) }}
        >
          {reload !== null ? 'Reload to stop' : props.stop.busy ? 'Stopping' : 'Stop'}
        </Button>
        {props.extra ?? null}
        <Button variant="ghost" data-control="diagnostics" onClick={props.onDiagnostics}>
          Diagnostics
        </Button>
      </div>
    </header>
  );
}
