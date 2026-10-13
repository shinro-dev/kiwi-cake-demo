// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAPABILITY CARD'S CHECK MODE: one prepared Plan applied to the robot's capability slot and read back, NO MOTION.
//
// It sits in the Cake panel's request slot, the same place and the same card shape as the mission's capability request
// (`CapabilityCard` in `design/Screen.tsx`), and it is drawn ONLY when no mission is active: while a mission is carried
// the slot belongs to that mission, and the runtime refuses a check under one anyway. The operator's brief asks for the
// existing non-motion activation controls to be driven against the LeKiwi resident; the mission's own request is offered
// only to a mission that is waiting, which a LIVE runtime cannot have yet, so this mode is how those controls are driven.
//
// WHAT IT SAYS AND WHAT IT NEVER SAYS. Its title is "Capability slot check (no motion)" and its button "Apply this prepared
// Plan". It never uses the mission's word for its outcome, never speaks of the robot going anywhere and never says that
// motion is enabled, because none of that is what a check does: it changes a Cake slot, the runtime refuses it unless the
// supervised host is held, and it sends nothing to the robot host.
//
// WHAT IT SENDS. The key picked from the runtime's own list, the digest the runtime showed beside that key, and the
// resident session of the CURRENT reading, which is printed on the card beside the button so the operator presses for a
// session they can see. Nothing else: there is no field here for bytes, a path or an operation.
//
// WHAT IT SHOWS BACK. The newest check the runtime recorded, with the slot read before and after drawn by the same
// component the mission's activations are drawn with (`ActivationEvidence`), and a RED row when the host was not held
// throughout or the resident session changed across it. The receipt for the press is kept apart from that record.

import type { CSSProperties, ReactNode } from 'react';
import { useId } from 'react';
import type { CapabilityCheckOutcome } from '../api/types';
import type { CapabilityCheckRecord, CapabilityCheckState } from '../model/capabilityCheck';
import {
  checkApplyOff,
  checkChoice,
  checkHeadline,
  checkHostWords,
  checkOffWords,
  checkReadingAgeMs,
  checkThroughoutWords,
  checkWarnings,
  slotChangeWords,
} from '../model/capabilityCheck';
import { ActivationEvidence } from './ActivationEvidence';
import { Button } from './Button';
import { C, INK, SURFACE, wash } from './theme';

const KICKER: CSSProperties = {
  fontSize: 11,
  letterSpacing: '.1em',
  textTransform: 'uppercase',
  color: 'var(--color-muted-foreground)',
};

const MONO: CSSProperties = { fontFamily: 'var(--font-mono), monospace', fontSize: 11, overflowWrap: 'anywhere' };

/** How many earlier checks are listed under the newest one. Every one of them is on the runtime's record. */
const EARLIER_SHOWN = 4;

const TONE: Record<'ok' | 'bad' | 'warn' | 'wait' | 'mute' | 'ink', string> = { ok: C.ok, bad: C.bad, warn: C.warn, wait: C.wait, mute: C.mute, ink: INK.bright };

export interface CapabilityCheckCardProps {
  state: CapabilityCheckState;
  readAtMs: number | null;
  nowMs: number;
  /** A read that failed after this state was read: the state is then retained and says so. */
  readError: string | null;
  chosen: string | null;
  onChoose: (key: string) => void;
  applying: boolean;
  lastCheck: CapabilityCheckOutcome | null;
  readOnlyReason: string | null;
  onApply: () => void;
}

export function CapabilityCheckCard(props: CapabilityCheckCardProps): ReactNode {
  const { state } = props;
  const reasonId = useId();
  const candidates = state.candidates;
  const chosen = checkChoice(state, props.chosen);
  const off = checkApplyOff({ state, readAtMs: props.readAtMs, nowMs: props.nowMs, applying: props.applying, chosen, readOnlyReason: props.readOnlyReason });
  const age = checkReadingAgeMs(state, props.readAtMs, props.nowMs);
  const newest = state.checks[state.checks.length - 1] ?? null;
  const earlier = state.checks.slice(Math.max(0, state.checks.length - 1 - EARLIER_SHOWN), -1).reverse();
  const current = state.current;

  return (
    <div
      data-capability-check=""
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
        padding: '12px 14px',
        border: '1px solid var(--color-border)',
        borderLeft: `3px solid ${C.accent}`,
        borderRadius: 10,
        background: SURFACE.raised,
        flex: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={KICKER}>Capability slot check (no motion)</span>
      </div>
      <div style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45, textWrap: 'pretty' }}>
        Applies one prepared Plan to the robot&apos;s capability slot and reads the slot back. It sends nothing to the robot host, the runtime
        refuses it unless the supervised host is held, and it is not a mission: nothing here starts one.
      </div>

      {/* WHAT A PRESS IS SENT AGAINST: the current reading's session, printed where the button is. */}
      <div data-check-current="" style={{ ...MONO, color: INK.quiet, lineHeight: 1.5 }}>
        resident session {current.session_uuid ?? 'not reported'} | host{' '}
        {checkHostWords({ host_running: current.host_running, held: current.host_held, reason_code: current.host_reason_code })} | slot{' '}
        {current.slot === null ? 'not read' : `generation ${current.slot.generation ?? 'not reported'}, capsule ${current.slot.capsule ?? 'not reported'}`} |
        reading {age === null ? 'of unknown age' : `${String(Math.round(age / 1000))} s old`}
      </div>
      {props.readError === null ? null : (
        <div style={{ fontSize: 12, color: C.warn }}>The last read of the check failed ({props.readError}); what is shown is the one before it.</div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <select
          data-control="check-candidate"
          aria-label="prepared Plan"
          value={chosen ?? ''}
          onChange={(event) => props.onChoose(event.target.value)}
          disabled={candidates.length === 0 || props.applying}
          aria-disabled={candidates.length === 0 || props.applying ? 'true' : 'false'}
          style={{
            font: '500 12px var(--font-mono), monospace',
            color: INK.body,
            background: SURFACE.chip,
            border: '1px solid var(--color-border)',
            borderRadius: 6,
            padding: '5px 8px',
            maxWidth: '100%',
            minWidth: 0,
          }}
        >
          {candidates.length === 0 ? <option value="">no prepared Plan</option> : null}
          {/* NOTHING IS PRESELECTED: until the operator picks one, the list says so and Apply stays off. */}
          {candidates.length > 0 && chosen === null ? <option value="">choose a prepared Plan</option> : null}
          {candidates.map((candidate) => {
            const role = state.roles.find((item) => item.key === candidate.key);
            return (
              <option key={candidate.key} value={candidate.key}>
                {role === undefined || role.role === null ? candidate.key : `${role.label} (${candidate.key})`}
              </option>
            );
          })}
        </select>
        <Button
          variant="default"
          data-control="capability-check-apply"
          disabled={off !== null}
          aria-disabled={off !== null ? 'true' : 'false'}
          aria-describedby={off === null ? undefined : reasonId}
          onClick={props.onApply}
          title={off ?? 'Send this Plan key, its digest and the session shown to the runtime, which applies it to the capability slot and reads the slot back.'}
        >
          {props.applying ? 'Applying' : 'Apply this prepared Plan'}
        </Button>
      </div>
      {chosen === null ? null : (
        <div style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>
          digest {candidates.find((item) => item.key === chosen)?.payload_digest ?? 'not reported'}
        </div>
      )}
      {off === null ? null : (
        <div id={reasonId} data-control-reason="capability-check-apply" style={{ fontSize: 12, color: C.warn, lineHeight: 1.45, textWrap: 'pretty' }}>
          Off: {off}.
        </div>
      )}

      {/* THE RECEIPT FOR THE PRESS, kept apart from the record below it in words as well as in place. */}
      {props.applying ? (
        <div style={{ fontSize: 12, color: C.wait, lineHeight: 1.45 }}>
          The request is in flight: this tab is waiting for the runtime&apos;s answer, and nothing is sent a second time.
        </div>
      ) : props.lastCheck === null ? null : (
        <div data-check-receipt="" style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45, textWrap: 'pretty' }}>
          {checkReceiptWords(props.lastCheck)}
        </div>
      )}

      {newest === null ? (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--color-muted-foreground)' }}>No check is on this runtime&apos;s record.</p>
      ) : (
        <CheckRecord check={newest} roles={state.roles} />
      )}
      {earlier.length === 0 ? null : (
        <div data-check-earlier="" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={KICKER}>Earlier checks, newest first</span>
          {earlier.map((check) => (
            <div key={check.operation_uuid} style={{ ...MONO, color: INK.quiet, lineHeight: 1.45 }}>
              {check.candidate_key ?? 'Plan not named'}: {checkHeadline(check, state.roles).words}; {slotChangeWords(check)}
              {checkWarnings(check).length === 0 ? '' : ' | NOT KEPT: see its warning in the record'}
            </div>
          ))}
          {state.checks_dropped === 0 ? null : (
            <span style={{ fontSize: 11, color: 'var(--color-muted-foreground)' }}>
              {String(state.checks_dropped)} older checks are on the record and not listed here.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** One recorded check: its headline, whether the slot changed, its red rows, and the evidence table. */
function CheckRecord(props: { check: CapabilityCheckRecord; roles: CapabilityCheckState['roles'] }): ReactNode {
  const { check } = props;
  const headline = checkHeadline(check, props.roles);
  const warnings = checkWarnings(check);
  return (
    <div data-check-record={check.operation_uuid} style={{ display: 'flex', flexDirection: 'column', gap: 4, paddingTop: 8, borderTop: '1px solid var(--color-border)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: TONE[headline.tone], flex: 'none' }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: TONE[headline.tone] }}>{headline.words}</span>
        <span style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>{check.candidate_key ?? 'Plan not named'}</span>
      </div>
      <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>{slotChangeWords(check)}.</div>
      <div style={{ ...MONO, color: 'var(--color-muted-foreground)', lineHeight: 1.5 }}>
        sent for session {check.requested_session_uuid ?? 'not recorded'} | host before {checkHostWords(check.host_before)} | host after{' '}
        {checkHostWords(check.host_after)} | {checkThroughoutWords(check)}
        {check.duration_ms === null ? '' : ` | ${String(Math.round(check.duration_ms))} ms from the press to the last read`}
      </div>
      {check.detail === null ? null : (
        <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>The runtime&apos;s detail: {check.detail.replace(/\.$/, '')}.</div>
      )}
      {warnings.map((warning) => (
        <div
          key={warning.key}
          role="alert"
          data-check-warning={warning.key}
          style={{ fontSize: 12, fontWeight: 600, color: '#fecaca', background: wash(C.bad, 0.1), border: `1px solid ${wash(C.bad, 0.35)}`, borderRadius: 6, padding: '5px 8px' }}
        >
          {warning.text}
        </div>
      ))}
      <ActivationEvidence activation={check} full />
    </div>
  );
}

/** The receipt for the last press from this tab, in words. It is never the record's verdict. */
export function checkReceiptWords(receipt: CapabilityCheckOutcome): string {
  if (receipt.kind === 'refused') {
    return `The runtime refused the last check from this tab and submitted nothing: ${checkOffWords(receipt.reason_code)}.`;
  }
  if (receipt.kind === 'unknown') {
    return `The answer to the last check from this tab could not be read: ${receipt.detail}. Whether it reached the resident is not known from here, nothing was sent again, and the record below is what the runtime holds.`;
  }
  return `The runtime took the last check from this tab and recorded ${receipt.activation_state}${
    receipt.transaction_phase === null ? '' : `, transaction phase ${receipt.transaction_phase}`
  }. That is a receipt for the request; what the slot and the host did is the record below.`;
}
