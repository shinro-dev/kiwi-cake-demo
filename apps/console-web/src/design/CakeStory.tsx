// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// CAKE ON THE ROBOT, top right (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 2.1, 3.3, 5.5, 5.6, 6.1).
//
// Every activation of the mission as its own card, oldest first, never replaced, merged or moved; the newest signature
// check, dated, muted as a whole when an earlier runtime session ran it; the live slot as a footer, with the record that
// set it. All of it sits in a bounded region that scrolls within the region's fixed footprint (spec 2.1) and follows the
// newest activation, so the result of the latest press is in view. While the mission waits for Cake, the prepared
// candidates, one button each, nothing preselected, are drawn ABOVE that region, outside it: the top bar sends the
// operator here to choose, and a hold that needs the operator is never below a fold (verify-r5 M2). The words are
// `model/cakeStory.ts`'s; which button is off is the caller's (the runtime's guards).

import type { ReactNode } from 'react';
import type { CakeStoryModel } from '../model/cakeStory';
import { BoundedAccount } from './BoundedAccount';
import { Button } from './Button';
import { C, INK, wash } from './theme';
import { TONE_COLOUR } from './TopBar';

const MONO = { fontFamily: 'var(--font-mono), monospace', fontSize: 12, overflowWrap: 'anywhere' as const };

export interface CakeStoryProps {
  model: CakeStoryModel;
  activating: boolean;
  /** Why no candidate may be pressed now (the runtime's guards, a stale read), or null. */
  activationOff: string | null;
  signatureRunOff: string | null;
  signatureRunning: boolean;
  /** Whether the runtime serves a signature check at all; with none, no Run button is drawn. Absent means served. */
  signatureServed?: boolean;
  onActivate: (key: string) => void;
  onRunSignature: () => void;
  notReadSince: string | null;
  dimmed?: boolean;
}

export function CakeStory(props: CakeStoryProps): ReactNode {
  const { model } = props;
  const signature = model.signature;
  const sigTone = TONE_COLOUR[signature.verdict.tone];
  return (
    <section
      aria-label="Cake on the robot"
      data-region="cake"
      data-cake-column="demo"
      style={{ minWidth: 0, height: '100%', boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 12, padding: '16px 18px', borderRadius: 12, border: '1px solid var(--color-border)', background: 'var(--color-card)', opacity: props.dimmed === true ? 0.6 : 1 }}
    >
      <div style={{ flex: 'none', display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: INK.quiet }}>Cake on the robot</span>
        <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>oldest first</span>
        {props.notReadSince === null ? null : (
          <span data-stale-marker="cake" style={{ fontSize: 12, color: C.warn }}>
            Not read since {props.notReadSince}
          </span>
        )}
      </div>

      {model.pending === null ? null : (
        <div data-pending-choice="" style={{ flex: 'none', maxHeight: '70%', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingBottom: 8, borderBottom: '1px solid var(--color-border)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: C.wait }}>The mission waits for Cake. The prepared Plans:</div>
          {model.pending.length === 0 ? (
            <div style={{ fontSize: 13, color: INK.quiet }}>The runtime serves no prepared Plan.</div>
          ) : (
            model.pending.map((item) => {
              const off = props.activationOff !== null || props.activating;
              return (
                <div key={item.key} style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <Button
                    variant={item.primary ? 'default' : 'outline'}
                    data-control="candidate"
                    data-candidate={item.key}
                    disabled={off}
                    aria-disabled={off ? 'true' : 'false'}
                    title={props.activationOff ?? `Send the key ${item.key}, and nothing else.`}
                    onClick={() => props.onActivate(item.key)}
                  >
                    {item.label}
                  </Button>
                  {item.digest === null ? null : (
                    <span style={{ ...MONO, color: INK.quiet }} title={item.digest_title ?? undefined}>
                      {item.digest}
                    </span>
                  )}
                </div>
              );
            })
          )}
          {props.activationOff === null ? null : <div style={{ fontSize: 12, color: INK.quiet }}>{props.activationOff}</div>}
        </div>
      )}

      {/* THE REGION'S FIXED FOOTPRINT (spec 2.1, "stable positions"): every card below is drawn, inside a region that
          scrolls within Cake's footprint, so the live strip under it never moves. */}
      <BoundedAccount name="cake" label="Cake's records" follow={model.cards.at(-1)?.id ?? null}>
        {model.cards.length === 0 ? (
          <div style={{ fontSize: 13, color: INK.quiet }}>{model.noCards}</div>
        ) : (
          <ol style={{ margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {model.cards.map((card) => {
              // A CAKE REFUSAL IS THE LOUDEST CARD, NOT THE QUIETEST (verdict 2026-09-27 item 12). Whatever expectation is
              // served, a refusal gets a larger, heavier headline, a 4 px rule and a full outline, in its verdict's colour:
              // green only for "Refused as expected", red for a refusal not as expected or with the slot changed, and with
              // no expectation served the neutral bright ink, which is neither good nor bad news. Every other card keeps its
              // verdict's tone at the regular weight.
              const loud = card.refusal;
              const colour = TONE_COLOUR[card.verdict.tone];
              // Longhands only: a `border` shorthand beside `borderLeft` is a style conflict React warns about on a re-render.
              const outline = loud ? `1px solid ${wash(colour, 0.35)}` : undefined;
              return (
                <li
                  key={card.id}
                  data-activation-card={card.id}
                  data-verdict={card.verdict.id}
                  data-refusal={card.refusal ? 'true' : undefined}
                  data-follow-target={card.id}
                  style={{
                    listStyle: 'none',
                    padding: '10px 12px',
                    borderRadius: 10,
                    borderTop: outline,
                    borderRight: outline,
                    borderBottom: outline,
                    borderLeft: `${loud ? 4 : 3}px solid ${colour}`,
                    background: wash(colour, loud ? 0.08 : 0.05),
                  }}
                >
                  <div style={{ fontSize: loud ? 16 : 14, fontWeight: loud ? 700 : 600, color: colour }}>{card.headline}</div>
                  <div style={{ ...MONO, color: INK.soft, marginTop: 2 }} title={[card.capsule_before_title, card.capsule_after_title].filter((item) => item !== null).join(' -> ')}>
                    {card.stamp === null ? card.line : <span data-t={card.stamp.iso}>{card.line}</span>}
                  </div>
                  {card.label === null ? null : <div style={{ fontSize: 12, color: INK.quiet, marginTop: 2 }}>{card.label}</div>}
                  {card.answer === null ? null : <div style={{ fontSize: 12, color: INK.quiet, marginTop: 2 }}>{card.answer}</div>}
                  {card.note === null ? null : <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', marginTop: 2 }}>{card.note}</div>}
                </li>
              );
            })}
          </ol>
        )}

        <div data-signature-card="" data-signature-stale={signature.stale ? 'true' : 'false'} data-verdict={signature.verdict.id} style={{ padding: '10px 12px', borderRadius: 10, border: '1px solid var(--color-border)', color: signature.stale ? C.mute : INK.body }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: signature.stale ? C.mute : sigTone }}>Signature check: {signature.verdict.word}</span>
            {signature.stamp === null || signature.stale ? null : (
              <span data-t={signature.stamp.iso} title={signature.stamp.title} style={{ fontSize: 12, color: INK.quiet }}>
                {signature.stamp.text}
              </span>
            )}
          </div>
          {signature.lines.map((line) => (
            <div key={line} style={{ fontSize: 12, color: signature.stale ? C.mute : INK.quiet, marginTop: 3, lineHeight: 1.45 }}>
              {line}
            </div>
          ))}
          {!model.signatureRunOffered || props.signatureServed === false ? null : (
            <div style={{ marginTop: 8 }}>
              <Button
                variant="outline"
                data-control="signature-check"
                disabled={props.signatureRunOff !== null || props.signatureRunning}
                aria-disabled={props.signatureRunOff !== null || props.signatureRunning ? 'true' : 'false'}
                title={props.signatureRunOff ?? 'Run the release verifier on the robot. No motion, no slot.'}
                onClick={props.onRunSignature}
              >
                {props.signatureRunning ? 'Running' : 'Run signature check'}
              </Button>
            </div>
          )}
        </div>

        {model.slot === null ? (
          <div data-live-slot="" style={{ fontSize: 12, color: INK.quiet }}>
            The robot has not been read, so the live slot is not known here.
          </div>
        ) : (
          <div data-live-slot="" style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45, borderTop: '1px solid var(--color-border)', paddingTop: 8 }}>
            <div>{model.slot.words}</div>
            <div style={{ color: INK.quiet }}>{model.slot.set_by}</div>
          </div>
        )}
      </BoundedAccount>
    </section>
  );
}
