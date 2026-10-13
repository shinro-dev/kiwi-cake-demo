// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE OUTCOME CARD, top left (docs/autonomous-demo/console-redesign-2026-09-27.md, sections 2.1, 3.2 and 6.2).
//
// The one loud element of the screen: the mission's word at 28 px in its verdict colour, a 4 px rule and a 6 percent wash
// of the same colour, with the mission id and its times, and Acknowledge in the head row. Under the head, in a bounded
// region that scrolls within the card's fixed footprint (spec 2.1, "stable positions"): the run line, the runtime's steps
// with the controls they name, what the mission check refused, why the run ended with its own measurement, and where the
// robot was left. An acknowledged ending collapses to one line in its own place, with the whole card behind Show. Every
// word is `model/outcomeCard.ts`'s.

import type { ReactNode } from 'react';
import type { OutcomeCardModel } from '../model/outcomeCard';
import { RUN_ENDED_READING } from '../model/verdict';
import { BoundedAccount } from './BoundedAccount';
import { Button } from './Button';
import { C, INK, wash } from './theme';
import { TONE_COLOUR } from './TopBar';

const MONO = { fontFamily: 'var(--font-mono), monospace', fontSize: 12, overflowWrap: 'anywhere' as const };

export interface OutcomeCardProps {
  model: OutcomeCardModel;
  /** The Return arm control, the Actions panel's own, drawn where the step list names it and beside a past mission's line. */
  returnArm: ReactNode;
  retrying: boolean;
  /** Why Try again is off now, or null. */
  retryOff?: string | null;
  onRetry: () => void;
  onAcknowledge: () => void;
  /** The fixed time of the mission's last good read when it is stale, or null. */
  notReadSince?: string | null;
  dimmed?: boolean;
  /** The address the failure's own frame is served at, or null when the source serves no picture. */
  frameUrl?: (id: string) => string | null;
  /** Opens History, where a mission that is not current is (spec section 12). */
  onHistory?: () => void;
}

/**
 * THE LINE OF A CARD WITH NO MISSION, from the read: "no mission yet" only when the runtime answered that it has none,
 * and a read that has not answered, or failed for another reason, says the mission was not read.
 */
function noMissionLine(missionError: string | null): string {
  if (missionError === 'no_mission_recorded') return 'No mission yet in this runtime session.';
  if (missionError === null) return 'The mission has not been read yet.';
  return `The mission has not been read: ${missionError}.`;
}

/** The runtime's step list, exactly and in order, with the controls it names (rule 6.2.4). */
function Steps(props: OutcomeCardProps & { heading: string }): ReactNode {
  const model = props.model;
  return (
    <>
      {model.steps.length === 0 ? null : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>{props.heading}</div>
          <ol data-steps="" style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {model.steps.map((step) => (
              <li key={step.token} data-step={step.token} style={{ fontSize: 14, color: INK.body, lineHeight: 1.45 }}>
                <span>{step.words}</span>
                {step.byHand ? (
                  <span data-by-hand="" style={{ marginLeft: 8, fontSize: 12, color: C.warn, border: `1px solid ${wash(C.warn, 0.4)}`, borderRadius: 5, padding: '0 6px' }}>
                    by hand
                  </span>
                ) : null}
                {step.control === 'return_arm' ? <div style={{ marginTop: 4 }}>{props.returnArm}</div> : null}
                {step.control === 'retry' ? (
                  <div style={{ marginTop: 4 }}>
                    <Button
                      variant={model.primary_step === step.token ? 'default' : 'secondary'}
                      data-control="retry"
                      disabled={props.retrying || (props.retryOff ?? null) !== null}
                      aria-disabled={props.retrying || (props.retryOff ?? null) !== null ? 'true' : 'false'}
                      title={props.retryOff ?? undefined}
                      onClick={props.onRetry}
                    >
                      {props.retrying ? 'Asking' : 'Try again'}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      )}
    </>
  );
}

/** The verdict word, the mission id and its times: the card's head, which stays in view above the bounded account. */
function Head(props: OutcomeCardProps): ReactNode {
  const model = props.model;
  const verdict = model.verdict;
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <span data-verdict={verdict?.id ?? 'none'} style={{ fontSize: 28, fontWeight: 650, lineHeight: 1.1, color: verdict === null ? C.mute : TONE_COLOUR[verdict.tone] }}>
          {verdict === null
            ? 'No mission'
            : verdict.id === RUN_ENDED_READING.id
              ? verdict.word
              : `Mission ${verdict.word.charAt(0).toLowerCase()}${verdict.word.slice(1)}`}
        </span>
        {model.mission_id === null ? null : <span style={{ ...MONO, color: INK.quiet }}>{model.mission_id}</span>}
        {/* THE TIMES ON THE VERDICT'S ROW, wrapping under it when the card is narrow: the head stays short, so more of the
            account shows at the card's fixed footprint (spec 2.1). */}
        <span style={{ fontSize: 13, color: INK.quiet, display: 'inline-flex', gap: 14, flexWrap: 'wrap' }}>
          {model.started === null ? null : (
            <span data-started="" data-t={model.started.iso} title={model.started.title}>
              Started {model.started.text}
            </span>
          )}
          {model.ended === null ? null : (
            <span data-ended="" data-t={model.ended.iso} title={model.ended.label}>
              Ended {model.ended.text}
            </span>
          )}
          {model.duration === null ? null : <span>{model.duration}</span>}
        </span>
      </div>
    </>
  );
}

/**
 * THE ACCOUNT, in the bounded region under the head (spec 2.1): the run line, the runtime's steps with their controls
 * (first, so a control the runtime names is in view at the card's fixed footprint), then what the robot did and what the
 * mission check refused, why it stopped with its own measurement and frame, and where the robot was left.
 */
function Account(props: OutcomeCardProps): ReactNode {
  const model = props.model;
  const run = model.run;
  return (
    <>
      {model.mission_detail === null ? null : (
        <div data-mission-detail="" style={{ fontSize: 14, color: INK.body, lineHeight: 1.45, overflowWrap: 'anywhere' }}>
          {model.mission_detail}
        </div>
      )}

      {run === null ? null : (
        <div data-run-line="" data-run-verdict={run.verdict?.id ?? 'none'} data-run-stage={run.step ?? 'none'} style={{ fontSize: 14, color: run.verdict === null ? INK.body : TONE_COLOUR[run.verdict.tone], lineHeight: 1.45, overflowWrap: 'anywhere' }}>
          {run.line}
          {run.stage === null && run.state === null ? null : (
            <span style={{ color: INK.soft }}>
              {' '}
              ({[run.stage, run.state].filter((item) => item !== null).join(', ')}
              {run.state_at === null ? '' : ` `}
              {run.state_at === null ? null : (
                <span data-t={run.state_at.iso} title={run.state_at.title}>
                  {run.state_at.text}
                </span>
              )}
              )
            </span>
          )}
        </div>
      )}

      {model.placement === null ? null : (
        <div data-outcome-placement="" style={{ fontSize: 14, color: INK.body, lineHeight: 1.45 }}>
          {model.placement}
        </div>
      )}

      {model.held === null ? null : (
        <div data-held="" style={{ fontSize: 14, color: INK.body, lineHeight: 1.45 }}>
          {model.held}
        </div>
      )}

      <Steps {...props} heading={"Next steps, in the runtime's order"} />

      {/* WHAT THE ROBOT DID AND WHAT THE MISSION CHECK REFUSED (spec 3.2.1), both, before the controller's own sentence. */}
      {model.check === null ? null : (
        <div data-mission-check={model.check.code} data-check-stage={model.check.stage} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>What the robot did, and what the mission check refused</div>
          <div data-check-did="" style={{ fontSize: 14, color: INK.body, lineHeight: 1.45 }}>
            {model.check.did}
          </div>
          <div data-check-refused="" style={{ fontSize: 14, color: INK.body, lineHeight: 1.45 }}>
            {model.check.refused}
          </div>
        </div>
      )}

      {model.cause === null ? null : (
        <div data-cause={model.cause.reason_code} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>Why it stopped, from the run&apos;s own records</div>
          {model.cause.detail === null ? null : <div style={{ fontSize: 13, color: INK.soft, lineHeight: 1.45 }}>{model.cause.detail}</div>}
          <code style={{ ...MONO, color: INK.quiet }}>{model.cause.reason_code}</code>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: INK.body, lineHeight: 1.45 }}>
            {model.cause.lines.map((line) => (
              <li key={line} data-cause-line="">
                {line}
              </li>
            ))}
          </ul>
          {model.cause.limit === null ? null : (
            <div data-failure-limit="" style={{ fontSize: 13, color: INK.soft }}>
              Decided by {model.cause.limit}
            </div>
          )}
          {/* THE FAILURE'S OWN FRAME, as run.finished names it, and no frame of any other record (audit C4). */}
          {model.cause.frame_id === null ? null : (
            <figure data-failure-frame={model.cause.frame_id} style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: 4, maxWidth: 320 }}>
              {props.frameUrl?.(model.cause.frame_id) ? (
                <img src={props.frameUrl(model.cause.frame_id) ?? undefined} alt="the frame the run's failure names" style={{ width: '100%', aspectRatio: '16 / 9', objectFit: 'contain', background: '#07070b', borderRadius: 6 }} />
              ) : null}
              <figcaption style={{ ...MONO, color: INK.quiet }}>
                {props.frameUrl?.(model.cause.frame_id) ? model.cause.frame_id : `${model.cause.frame_id} (this source serves no picture of it)`}
              </figcaption>
            </figure>
          )}
        </div>
      )}

      {model.where === null ? null : (
        <div data-where="" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>Where the robot was left</div>
          <div data-holding={model.where.holding === null ? 'unknown' : String(model.where.holding)} style={{ fontSize: 14, color: model.where.holding === false ? INK.body : C.warn }}>
            {model.where.holding_words}
            {model.where.gripper === null ? '' : `, ${model.where.gripper}`}
            {model.where.gripper_at === null ? null : (
              <span data-t={model.where.gripper_at.iso} title={model.where.gripper_at.title} style={{ color: INK.quiet }}>
                {' '}
                ({model.where.gripper_at.text})
              </span>
            )}
          </div>
          <div style={{ fontSize: 13, color: INK.soft }}>
            {model.where.base}. {model.where.arm}.
          </div>
        </div>
      )}
    </>
  );
}

export function OutcomeCard(props: OutcomeCardProps): ReactNode {
  const model = props.model;
  const tone = model.verdict === null ? C.mute : TONE_COLOUR[model.verdict.tone];
  const state =
    model.kind === 'none' ? 'none' : model.kind === 'past' ? 'past' : model.kind === 'carried' ? 'carried' : model.acknowledged === null ? 'expanded' : 'collapsed';
  return (
    <section
      aria-label="Mission"
      data-region="mission"
      data-outcome-card={state}
      style={{
        minWidth: 0,
        // THE CARD'S FOOTPRINT IS ITS REGION'S (design.css, spec 2.1): the head stays, the account scrolls inside.
        height: '100%',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        padding: '16px 18px',
        borderRadius: 12,
        background: wash(tone, 0.06),
        // LONGHANDS ONLY: a `border` shorthand beside the left longhands makes React warn on every change of tone.
        borderTopWidth: 1,
        borderTopStyle: 'solid',
        borderTopColor: wash(tone, 0.25),
        borderRightWidth: 1,
        borderRightStyle: 'solid',
        borderRightColor: wash(tone, 0.25),
        borderBottomWidth: 1,
        borderBottomStyle: 'solid',
        borderBottomColor: wash(tone, 0.25),
        borderLeftWidth: 4,
        borderLeftStyle: 'solid',
        borderLeftColor: tone,
        opacity: props.dimmed === true ? 0.6 : 1,
      }}
    >
      <div style={{ flex: 'none', display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: INK.quiet }}>Mission</span>
        <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>from the mission record</span>
        {props.notReadSince === null || props.notReadSince === undefined ? null : (
          <span data-stale-marker="mission" style={{ fontSize: 12, color: C.warn }}>
            Not read since {props.notReadSince}
          </span>
        )}
        {/* ACKNOWLEDGE IN THE HEAD ROW (spec 2.1): in view at the card's fixed footprint, whatever the account's length.
            HISTORY ONE CLICK AWAY in every state (spec 12.3; verify-r4 S1): a past mission's line carries its own button. */}
        {state === 'expanded' || (state !== 'past' && props.onHistory !== undefined) ? (
          <span style={{ marginLeft: 'auto', alignSelf: 'center', display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>
            {props.onHistory === undefined ? null : (
              <Button variant="ghost" data-control="open-history" aria-disabled="false" onClick={props.onHistory} title="Open History, where the missions of earlier sessions and days are, with their dates.">
                History
              </Button>
            )}
            {state === 'expanded' ? (
              <Button variant="secondary" data-control="acknowledge" aria-disabled="false" onClick={props.onAcknowledge} title="Collapse this card on this display. Nothing is sent to the runtime.">
                Acknowledge
              </Button>
            ) : null}
          </span>
        ) : null}
      </div>
      {model.kind === 'none' ? (
        <BoundedAccount name="mission" label="The mission">
          <div style={{ fontSize: 15, color: INK.soft }}>{noMissionLine(model.mission_error)}</div>
        </BoundedAccount>
      ) : state === 'past' ? (
        // HISTORY'S MISSION (spec section 12): one neutral dated line, never a verdict card, and the way to History.
        <BoundedAccount name="mission" label="The mission">
          <div style={{ fontSize: 15, color: INK.soft }}>{model.past_none}</div>
          <div data-last-mission="" style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', fontSize: 13, color: INK.quiet }}>
            <span>{model.past_line}</span>
            {props.onHistory === undefined ? null : (
              <Button variant="outline" data-control="open-history" aria-disabled="false" onClick={props.onHistory} title="Open History, where the missions of earlier sessions and days are, with their dates.">
                History
              </Button>
            )}
          </div>
          {/* RETURN ARM STAYS ONE PRESS AWAY (verify-r1 M2): the control the Actions panel draws, on or off by the
              capability list the runtime serves now, not by the step list recorded at that mission's end. */}
          <div data-past-return-arm="" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {props.returnArm}
          </div>
        </BoundedAccount>
      ) : state === 'collapsed' ? (
        <BoundedAccount name="mission" label="The mission">
          <div data-collapsed-line="" style={{ fontSize: 14, color: INK.soft }}>
            {model.collapsed_line}
          </div>
          <details>
            <summary data-control="show-outcome" style={{ cursor: 'pointer', fontSize: 13, color: INK.quiet }}>
              Show
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 8 }}>
              <Head {...props} />
              <Account {...props} />
            </div>
          </details>
        </BoundedAccount>
      ) : (
        <>
          <div style={{ flex: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Head {...props} />
          </div>
          <BoundedAccount name="mission" label="The mission's account">
            <Account {...props} />
          </BoundedAccount>
        </>
      )}
    </section>
  );
}
