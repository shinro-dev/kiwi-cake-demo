// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE SIGNATURE POLICY CHECK BLOCK: the release's own verifier run on the robot over every deployed capsule, NO MOTION.
//
// It sits in the Cake panel directly under the capability slot check card, in the same card shape, and it is drawn ONLY
// when no mission is active: the runtime refuses it under one anyway. The operator's ruling asks the console to showcase
// signature module verification; the resident decides signature policy only when it starts and has no live admission
// operation, so what can be shown from the browser is the verifier the release ships (`demo-plan verify --policy
// require`), run on the robot under the resident's own trusted keys, and this block says exactly that in its honesty
// sentence. It never claims a live admission and never calls the slot check's abort-swap a signature refusal.
//
// WHAT IT SENDS. Nothing. The request is an empty body with the guard's headers: the verifier, the store, the keys and
// the policy are the deployed release's own. The store refuses a second press while one is in flight.
//
// WHAT IT SHOWS BACK. The newest run the runtime recorded as a verdict table, one row per capsule and key: the capsule's
// short digest, the key, what was expected, what was observed, the reason code and whether it was admitted (yes or no). A
// row the runtime SKIPPED (no unsigned twin staged) is drawn as skipped, never as a pass, and the run is then not complete.

import type { CSSProperties, ReactNode } from 'react';
import { useId } from 'react';
import type { SignatureCheckOutcome } from '../api/types';
import type { SignatureCheckRecord, SignatureCheckState, SignatureRow } from '../model/signatureCheck';
import {
  shortDigest,
  signatureAdmittedWords,
  signatureOffWords,
  signatureRunOff,
  signatureSummaryWords,
  signatureUnavailableWords,
  signatureVerdictWords,
} from '../model/signatureCheck';
import { earlierSessionLine } from '../model/cakeStory';
import { signatureVerdict } from '../model/verdict';
import { Button } from './Button';
import { C, INK, SURFACE } from './theme';

const KICKER: CSSProperties = {
  fontSize: 11,
  letterSpacing: '.1em',
  textTransform: 'uppercase',
  color: 'var(--color-muted-foreground)',
};

const PLAIN_KICKER: CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--color-muted-foreground)' };

const MONO: CSSProperties = { fontFamily: 'var(--font-mono), monospace', fontSize: 11, overflowWrap: 'anywhere' };

const CELL: CSSProperties = { padding: '3px 6px', textAlign: 'left', verticalAlign: 'top', borderBottom: '1px solid var(--color-border)' };

/** The honesty sentence, on screen with every run and with the empty record alike. */
// GENERAL: how the resident works, never a claim about this runtime's current state (verifier r2 R-3). The policy is the
// record's, drawn beside the Run button.
export const SIGNATURE_HONESTY =
  'This is the release verifier under the resident\'s signature policy. The resident admits capsules when it starts and has no live admission operation, so the abort-swap candidate is a transactional activation failure, not a signature refusal.';

export interface SignatureCheckBlockProps {
  /** The last good read, or null before one has come back. */
  state: SignatureCheckState | null;
  /** The reason the last read failed, or the runtime's refusal of the read (a runtime with no check). */
  readError: string | null;
  readErrorDetail: string | null;
  /** Whether the runtime is LIVE: a rehearsal that offers no check says nothing at all. */
  live: boolean;
  running: boolean;
  lastRun: SignatureCheckOutcome | null;
  readOnlyReason: string | null;
  onRun: () => void;
  /** Draw the record only, with no Run button and no receipt: the Run button stays on the main card (spec 7.1). */
  rowsOnly?: boolean;
  /**
   * The runtime session the snapshot names now. Given, a newest check from another session is marked as the main card
   * marks it (rule 5.6): muted, `data-signature-stale`, and the earlier-session sentence.
   */
  runtimeSession?: string | null;
  /** Labels in sentence case with no transform, as Diagnostics writes them (spec section 1); the classic screen keeps its own. */
  plainLabels?: boolean;
}

/**
 * The block, or the one-line reason a LIVE runtime offers none, or nothing (a rehearsal). It decides that itself so the
 * screen's insertion stays one element.
 */
export function SignatureCheckBlock(props: SignatureCheckBlockProps): ReactNode {
  if (props.state === null) {
    if (props.live && props.readError !== null) {
      return (
        <div data-signature-check-off="" style={{ fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.45, textWrap: 'pretty' }}>
          Signature policy check (no motion): not offered here, because {signatureUnavailableWords(props.readError, props.readErrorDetail)}.
        </div>
      );
    }
    return null;
  }
  return <SignatureCheckCard {...props} state={props.state} />;
}

function SignatureCheckCard(props: SignatureCheckBlockProps & { state: SignatureCheckState }): ReactNode {
  const { state } = props;
  const reasonId = useId();
  const off = signatureRunOff({ state, running: props.running, readOnlyReason: props.readOnlyReason });
  const newest = state.checks[state.checks.length - 1] ?? null;
  const earlier = state.checks.length - 1 + state.checks_dropped;
  const staleSession = props.runtimeSession !== undefined && newest !== null && signatureVerdict(newest, props.runtimeSession).id === 'sig_earlier';

  return (
    <div
      data-signature-check=""
      data-signature-stale={props.runtimeSession === undefined ? undefined : staleSession ? 'true' : 'false'}
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
        <span style={props.plainLabels === true ? PLAIN_KICKER : KICKER}>Signature policy check (no motion)</span>
      </div>
      {!staleSession || newest === null ? null : (
        <div data-signature-earlier="" style={{ fontSize: 13, color: C.mute, lineHeight: 1.45 }}>
          {earlierSessionLine(newest)}
        </div>
      )}
      <div style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45, textWrap: 'pretty' }}>
        Runs the release&apos;s own verifier on the robot over every deployed capsule: under the resident&apos;s trusted keys, under a throwaway key the
        runtime generates and discards, and for any unsigned twin the release staged. It sends nothing to the robot host, touches no slot and asks the
        resident nothing.
      </div>
      <div data-signature-honesty="" style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45, textWrap: 'pretty' }}>
        {SIGNATURE_HONESTY}
      </div>
      {props.readError === null ? null : (
        <div style={{ fontSize: 12, color: C.warn }}>The last read of the check failed ({props.readError}); what is shown is the one before it.</div>
      )}

      {props.rowsOnly === true ? null : (<>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Button
          variant="default"
          data-control="signature-check-run"
          disabled={off !== null}
          aria-disabled={off !== null ? 'true' : 'false'}
          aria-describedby={off === null ? undefined : reasonId}
          onClick={props.onRun}
          title={off ?? 'Ask the runtime to run the release verifier on the robot over every deployed capsule and record the verdict table.'}
        >
          {props.running ? 'Running' : 'Run the signature check'}
        </Button>
        <span style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>policy {state.policy ?? 'not reported'}</span>
      </div>
      {off === null ? null : (
        <div id={reasonId} data-control-reason="signature-check-run" style={{ fontSize: 12, color: C.warn, lineHeight: 1.45, textWrap: 'pretty' }}>
          Off: {off}.
        </div>
      )}

      {/* THE RECEIPT FOR THE PRESS, kept apart from the record below it. */}
      {props.running ? (
        <div style={{ fontSize: 12, color: C.wait, lineHeight: 1.45 }}>
          The request is in flight: this tab is waiting for the runtime&apos;s answer, and nothing is sent a second time.
        </div>
      ) : props.lastRun === null ? null : (
        <div data-signature-receipt="" style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45, textWrap: 'pretty' }}>
          {signatureReceiptWords(props.lastRun)}
        </div>
      )}
      </>)}

      {newest === null ? (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--color-muted-foreground)' }}>No signature check is on this runtime&apos;s record.</p>
      ) : (
        <SignatureRun check={newest} muted={staleSession} />
      )}
      {earlier <= 0 ? null : (
        <span style={{ fontSize: 11, color: 'var(--color-muted-foreground)' }}>
          {String(earlier)} earlier {earlier === 1 ? 'run is' : 'runs are'} on the record and not drawn here.
        </span>
      )}
    </div>
  );
}

/** One recorded run: its summary line, the verdict table and what it was verified under. */
function SignatureRun(props: { check: SignatureCheckRecord; muted?: boolean }): ReactNode {
  const { check } = props;
  // AN EARLIER SESSION'S CHECK IS MUTE WHOLE (spec 5.1 sig_earlier, 5.6), its verdict line and rule too, as on the main
  // card: it says nothing about this session (verifier r2 defect 8).
  const tone = props.muted === true ? C.mute : check.as_expected === true ? (check.complete === true ? C.ok : C.warn) : C.bad;
  return (
    <div data-signature-run={check.check_uuid} style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 8, borderTop: '1px solid var(--color-border)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: tone, flex: 'none' }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: tone }}>{signatureSummaryWords(check)}</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table data-signature-table="" style={{ ...MONO, borderCollapse: 'collapse', width: '100%', color: props.muted === true ? C.mute : INK.body }}>
          <thead>
            <tr style={{ color: 'var(--color-muted-foreground)' }}>
              <th style={CELL}>capsule</th>
              <th style={CELL}>key</th>
              <th style={CELL}>expected</th>
              <th style={CELL}>observed</th>
              <th style={CELL}>reason code</th>
              <th style={CELL}>admitted</th>
            </tr>
          </thead>
          <tbody>
            {check.rows.map((row, index) => (
              <SignatureRowLine key={`${row.kind}-${row.key}-${row.capsule ?? 'none'}-${String(index)}`} row={row} muted={props.muted === true} />
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ ...MONO, color: 'var(--color-muted-foreground)', lineHeight: 1.5 }}>
        verified under the resident&apos;s {String(check.trusted_keys.length)} trusted key{check.trusted_keys.length === 1 ? '' : 's'}
        {check.trusted_keys.length === 0 ? '' : ` (${check.trusted_keys.map((key) => key.slice(0, 12)).join(', ')})`} | throwaway key{' '}
        {check.throwaway_public_key === null ? 'not recorded' : `${check.throwaway_public_key.slice(0, 12)} (discarded)`} |{' '}
        {check.capsules === null ? 'capsule count not recorded' : `${String(check.capsules)} deployed capsule${check.capsules === 1 ? '' : 's'}`}
        {check.duration_ms === null ? '' : ` | ${String(Math.round(check.duration_ms))} ms`}
        {check.t_wall_utc === null ? '' : ` | ${check.t_wall_utc}`}
      </div>
    </div>
  );
}

function SignatureRowLine(props: { row: SignatureRow; muted?: boolean }): ReactNode {
  const { row } = props;
  // An earlier session's rows are muted whole, as the main card is (rule 5.6).
  const colour = props.muted === true ? C.mute : row.observed === 'skipped' ? INK.quiet : row.observed === 'error' || row.as_expected === false ? C.bad : INK.body;
  const label = row.kind === 'unsigned_twin' ? 'unsigned twin' : row.key;
  return (
    <tr data-signature-row={row.observed} style={{ color: colour }}>
      <td style={CELL} title={row.capsule ?? undefined}>{shortDigest(row.capsule)}</td>
      <td style={CELL}>{label}</td>
      <td style={CELL}>{row.expected}{row.expected_reason === null ? '' : ` ${row.expected_reason}`}</td>
      <td style={CELL}>{row.observed}</td>
      <td style={CELL}>{row.reason_code ?? 'none'}</td>
      <td style={CELL}>
        {signatureAdmittedWords(row.admitted)} | {signatureVerdictWords(row)}
      </td>
    </tr>
  );
}

/** The receipt for the last press from this tab, in words. It is never the record's verdict. */
export function signatureReceiptWords(receipt: SignatureCheckOutcome): string {
  if (receipt.kind === 'refused') {
    return `The runtime refused the last signature check from this tab and ran nothing: ${signatureOffWords(receipt.reason_code)}${
      receipt.detail === null ? '' : `, ${receipt.detail}`
    }.`;
  }
  if (receipt.kind === 'unknown') {
    return `The answer to the last signature check from this tab could not be read: ${receipt.detail}. Whether the verifier ran is not known from here, nothing was sent again, and the record below is what the runtime holds.`;
  }
  return `The runtime ran the verifier and recorded a run: ${String(receipt.rows_as_expected)} rows as expected, ${String(receipt.rows_not_as_expected)} not, ${String(
    receipt.rows_error,
  )} in error, ${String(receipt.rows_skipped)} skipped. That is a receipt for the request; the table below is the record.`;
}
