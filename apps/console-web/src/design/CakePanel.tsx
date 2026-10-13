// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE CAKE PANEL: what the robot's resident holds, what each prepared Plan is for, and what Cake did with the last one.
//
// It is the showcase the operator's brief asks for, built only from what the runtime records:
//   - one card per module the resident lists, with its Revision (capsule digest and generation), its state, pid,
//     spawns and restarts, and the controller's own card, which says it is not a Cake module (`design/cakeModules.ts`);
//   - one labelled button per PREPARED candidate, with the outcome its role expects. Nothing is preselected, and the
//     activate role is the only primary button. A press sends that key; the runtime adds the digest and the session;
//   - the newest check before and after: its headline, whether it met its role's expectation, ValidatePlan's verdict on
//     the exact bytes, the transaction's state path as the resident's flight ring recorded it, and the slot read before
//     and after (`ActivationEvidence`);
//   - the history of checks, newest first; the signature table (mounted by the screen); whether a Cake module took part
//     in the task, read from the mission record; and the honesty lines.
//
// IT DECIDES NOTHING. Verdicts are the runtime's and their words are `model/capabilityCheck.ts`'s; which button is off
// and why is `checkApplyOff`, the same rule the classic card uses. A press is the capability slot check (NO MOTION): the
// runtime refuses it unless the supervised host is held.

import type { CSSProperties, ReactNode } from 'react';
import type { CakeParticipation } from '../model/cakeTask';
import type { CandidateRole, CapabilityCheckRecord, CapabilityCheckState } from '../model/capabilityCheck';
import {
  checkApplyOff,
  checkExpectation,
  checkHeadline,
  checkWarnings,
  expectWords,
  slotChangeWords,
  statePathWords,
  validationWords,
} from '../model/capabilityCheck';
import { ActivationEvidence } from './ActivationEvidence';
import { Button } from './Button';
import type { HostFinding, ModuleCard } from './cakeModules';
import { CAKE_HONESTY_LINES, processWords, revisionWords } from './cakeModules';
import { C, INK, SURFACE, wash } from './theme';

// Sentence case, as the demo screen writes its headings: this panel is drawn in Diagnostics only (spec section 1).
const KICKER: CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--color-muted-foreground)' };
const MONO: CSSProperties = { fontFamily: 'var(--font-mono), monospace', fontSize: 11, overflowWrap: 'anywhere' };
const TONE: Record<'ok' | 'bad' | 'warn' | 'wait' | 'mute' | 'ink', string> = { ok: C.ok, bad: C.bad, warn: C.warn, wait: C.wait, mute: C.mute, ink: INK.bright };
/** How many earlier checks the history lists. Every one of them is on the runtime's record. */
const HISTORY_SHOWN = 6;

export interface CakePanelProps {
  /** The capability slot check as the runtime serves it, or null when the runtime offers none. */
  check: CapabilityCheckState | null;
  /** Why the runtime offers no check, in words, when `check` is null. */
  checkUnavailable: string | null;
  readAtMs: number | null;
  nowMs: number;
  /** The UTC day times are printed against (the mission's start date, or today's); null prints every date. */
  day?: string | null;
  applying: boolean;
  readOnlyReason: string | null;
  /** The check read's code when it failed and no check is held: the newest-check line says it was not read. */
  checkNotRead?: string | null;
  /** Send ONE prepared candidate's key. The screen adds its digest and the session the reading shows. */
  onApply: (key: string) => void;
  modules: ModuleCard[];
  participation: CakeParticipation | null;
  /** The signature table, labelled by what ran each row (`SignatureCheckBlock`), mounted by the screen. */
  signature: ReactNode;
  /** A motor host running outside Cake (`hostOutsideCake`), drawn red above the modules. Absent or null: none found. */
  hostFinding?: HostFinding | null;
  /**
   * Why the resident's modules are not drawn, while no reading of the robot is held (verify-r2 must_fix 4): the read's
   * code, or that it has not been read yet. Absent or null: the modules were read, and `modules` is what they listed.
   */
  modulesNotRead?: string | null;
  /** The newest read of the robot failed while an earlier reading is held: its code, said beside the held cards. */
  modulesReadFailed?: string | null;
}

export interface CandidateButton {
  key: string;
  label: string;
  role: CandidateRole | null;
  expect: string;
  digest: string | null;
  primary: boolean;
  press: (onApply: (key: string) => void) => void;
}

/** The candidates as buttons: the activate role first (the only primary one), then the rest in the runtime's order. */
export function candidateButtons(state: CapabilityCheckState): CandidateButton[] {
  const items = state.roles.map((role) => ({
    key: role.key,
    label: role.label,
    role: role.role,
    expect: expectWords(role.expect),
    digest: role.payload_digest,
    primary: role.role === 'activate',
    press: (onApply: (key: string) => void) => onApply(role.key),
  }));
  return [...items.filter((item) => item.primary), ...items.filter((item) => !item.primary)];
}

function Section(props: { title: string; children: ReactNode; data?: string }): ReactNode {
  return (
    <section data-cake-section={props.data ?? props.title} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={KICKER}>{props.title}</span>
      {props.children}
    </section>
  );
}

function ModuleCardView(props: { card: ModuleCard }): ReactNode {
  const { card } = props;
  return (
    <div
      data-cake-module={card.name}
      style={{ display: 'flex', flexDirection: 'column', gap: 3, padding: '8px 10px', border: '1px solid var(--color-border)', borderRadius: 8, background: SURFACE.raised }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: INK.bright }}>{card.name}</span>
        <span style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>{card.kind}</span>
      </div>
      <div style={{ ...MONO, color: INK.soft }} title={card.capsule_digest ?? undefined}>
        {revisionWords(card)}
      </div>
      <div style={{ ...MONO, color: INK.quiet }}>{processWords(card)}</div>
      {card.kind === 'controller' ? null : (
        <div style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>
          {card.config_digest === null ? 'config digest not reported by this surface' : `config ${card.config_digest.slice(0, 12)}`}
        </div>
      )}
      <div style={{ fontSize: 11, color: 'var(--color-muted-foreground)', lineHeight: 1.4 }}>managed by {card.managed_by}</div>
      {card.note === null ? null : <div style={{ fontSize: 11, color: card.kind === 'controller' ? C.warn : INK.quiet, lineHeight: 1.4 }}>{card.note}</div>}
    </div>
  );
}

/** The resident session a check ran under: its slot read's, else the one it asked for. */
function checkSession(check: CapabilityCheckRecord): string | null {
  return check.slot_after?.session_uuid ?? check.slot_before?.session_uuid ?? check.requested_session_uuid;
}

function NewestCheck(props: { check: CapabilityCheckRecord; state: CapabilityCheckState }): ReactNode {
  const { check, state } = props;
  const headline = checkHeadline(check, state.roles);
  const expectation = checkExpectation(check, state.roles);
  const label = state.roles.find((item) => item.key === check.candidate_key)?.label ?? check.candidate_key ?? 'Plan not named';
  return (
    <div
      data-cake-newest={check.operation_uuid}
      data-check-session={checkSession(check) ?? 'not recorded'}
      data-check-t={check.t_wall_utc ?? ''}
      style={{ display: 'flex', flexDirection: 'column', gap: 5, paddingTop: 8, borderTop: '1px solid var(--color-border)' }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: TONE[headline.tone], flex: 'none' }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: TONE[headline.tone] }}>{headline.words}</span>
      </div>
      {/* WHEN AND UNDER WHICH RESIDENT SESSION (audit C1): a check of an earlier session says so beside it. */}
      <div style={{ ...MONO, color: INK.quiet }}>
        {check.t_wall_utc ?? 'date not recorded'}, resident session {checkSession(check) ?? 'not recorded'}
        {state.current.session_uuid !== null && checkSession(check) !== null && checkSession(check) !== state.current.session_uuid ? ' (an earlier session than the current reading)' : ''}
      </div>
      <div style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>
        {label} ({check.candidate_key ?? 'not named'})
      </div>
      <div data-cake-expectation={expectation.met === null ? 'none' : String(expectation.met)} style={{ fontSize: 12, color: expectation.met === false ? C.bad : INK.quiet }}>
        {expectation.words}
      </div>
      <div style={{ fontSize: 12, color: INK.quiet }}>{validationWords(check)}.</div>
      <div data-cake-state-path="" style={{ ...MONO, color: INK.soft, lineHeight: 1.5 }}>
        {statePathWords(check)}
      </div>
      <div style={{ fontSize: 12, color: INK.quiet }}>{slotChangeWords(check)}.</div>
      {check.detail === null ? null : (
        <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>The runtime&apos;s detail: {check.detail.replace(/\.$/, '')}.</div>
      )}
      {checkWarnings(check).map((warning) => (
        <div
          key={warning.key}
          role="alert"
          style={{ fontSize: 12, fontWeight: 600, color: '#fecaca', background: wash(C.bad, 0.1), border: `1px solid ${wash(C.bad, 0.35)}`, borderRadius: 6, padding: '5px 8px' }}
        >
          {warning.text}
        </div>
      ))}
      <ActivationEvidence activation={check} />
    </div>
  );
}

export function CakePanel(props: CakePanelProps): ReactNode {
  const { check } = props;
  const buttons = check === null ? [] : candidateButtons(check);
  const offOf = (key: string): string | null =>
    check === null
      ? (props.checkUnavailable ?? 'the runtime offers no check')
      : checkApplyOff({ state: check, readAtMs: props.readAtMs, nowMs: props.nowMs, applying: props.applying, chosen: key, readOnlyReason: props.readOnlyReason, day: props.day ?? null });
  const offs = buttons.map((item) => offOf(item.key));
  const sharedOff = offs.length > 0 && offs.every((off) => off !== null && off === offs[0]) ? offs[0] : null;
  const newest = check === null ? null : (check.checks[check.checks.length - 1] ?? null);
  const earlier = check === null ? [] : check.checks.slice(Math.max(0, check.checks.length - 1 - HISTORY_SHOWN), -1).reverse();
  const participation = props.participation;
  const resident = props.modules.filter((card) => card.kind !== 'controller');
  const controllers = props.modules.filter((card) => card.kind === 'controller');

  return (
    <div data-cake-panel="" style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '12px 14px', border: '1px solid var(--color-border)', borderLeft: `3px solid ${C.accent}`, borderRadius: 10, background: SURFACE.raised }}>
      {props.hostFinding === null || props.hostFinding === undefined ? null : (
        <div
          role="alert"
          data-cake-finding={props.hostFinding.code}
          style={{ fontSize: 12, fontWeight: 600, color: '#fecaca', background: wash(C.bad, 0.1), border: `1px solid ${wash(C.bad, 0.35)}`, borderRadius: 6, padding: '6px 8px', lineHeight: 1.45 }}
        >
          {props.hostFinding.sentence}
        </div>
      )}
      <Section title="Modules the resident holds" data="modules">
        {props.modulesNotRead !== undefined && props.modulesNotRead !== null ? (
          <span data-modules-not-read="" style={{ fontSize: 12, color: C.warn }}>
            {props.modulesNotRead}
          </span>
        ) : resident.length === 0 ? (
          <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>The resident has listed no slot to this runtime.</span>
        ) : (
          <>
            {props.modulesReadFailed === undefined || props.modulesReadFailed === null ? null : (
              <span style={{ fontSize: 12, color: C.warn }}>
                The newest read of the resident failed ({props.modulesReadFailed}); these cards are from an earlier read.
              </span>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
              {resident.map((card) => (
                <ModuleCardView key={`${card.kind}:${card.name}`} card={card} />
              ))}
            </div>
          </>
        )}
      </Section>
      {/* THE CONTROLLER IS THIS RUNTIME'S CHILD, NOT THE RESIDENT'S (verify-r2 must_fix 4): its card is never listed under
          the resident's modules, whatever was or was not read. */}
      {controllers.length === 0 ? null : (
        <Section title="This runtime's controller (not a Cake module)" data="controller">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
            {controllers.map((card) => (
              <ModuleCardView key={`${card.kind}:${card.name}`} card={card} />
            ))}
          </div>
        </Section>
      )}

      <Section title="Prepared Plans (no motion)" data="candidates">
        <div style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45 }}>
          Each button applies one prepared Plan to the robot&apos;s capability slot and reads the slot back. It sends nothing to the robot
          host, and the runtime refuses it unless the supervised host is held.
        </div>
        {check === null ? (
          <div style={{ fontSize: 12, color: C.warn }}>No Plan can be applied from here: {props.checkUnavailable ?? 'the runtime offers no check'}.</div>
        ) : buttons.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>The runtime has offered no prepared Plan.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {buttons.map((item, index) => {
              const off = offs[index] ?? null;
              return (
                <div key={item.key} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <Button
                      variant={item.primary ? 'default' : 'outline'}
                      data-cake-candidate={item.key}
                      disabled={off !== null}
                      aria-disabled={off !== null ? 'true' : 'false'}
                      title={off ?? `Apply ${item.key}`}
                      onClick={() => item.press(props.onApply)}
                    >
                      {item.label}
                    </Button>
                  </div>
                  <span style={{ fontSize: 11, color: INK.quiet }}>{item.expect}</span>
                  <span style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>
                    {item.key} | digest {item.digest === null ? 'not reported' : item.digest.replace(/^sha256:/, '').slice(0, 12)}
                  </span>
                  {off !== null && sharedOff === null ? <span style={{ fontSize: 11, color: C.warn }}>Off: {off}.</span> : null}
                </div>
              );
            })}
            {sharedOff === null ? null : (
              <div data-control-reason="cake-candidates" style={{ fontSize: 12, color: C.warn, lineHeight: 1.45 }}>
                Off: {sharedOff}.
              </div>
            )}
            {props.applying ? (
              <div style={{ fontSize: 12, color: C.wait }}>A request is in flight from this tab; nothing is sent a second time.</div>
            ) : null}
          </div>
        )}
      </Section>

      <Section title="The newest slot check, with its session and date" data="newest">
        {check === null && (props.checkNotRead ?? null) !== null ? (
          // A CHECK READ THAT FAILED is not an empty record (verify-r1 must_fix 3).
          <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>The slot check has not been read: {props.checkNotRead}.</span>
        ) : newest === null || check === null ? (
          <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>No check is on this runtime&apos;s record.</span>
        ) : (
          <NewestCheck check={newest} state={check} />
        )}
      </Section>

      {earlier.length === 0 || check === null ? null : (
        <div data-cake-history="" style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={KICKER}>Earlier checks, newest first</span>
          {earlier.map((item) => (
            <div key={item.operation_uuid} style={{ ...MONO, color: INK.quiet, lineHeight: 1.45 }}>
              {item.candidate_key ?? 'Plan not named'}: {checkHeadline(item, check.roles).words}; {checkExpectation(item, check.roles).words}; {statePathWords(item)}
            </div>
          ))}
          {check.checks_dropped === 0 ? null : (
            <span style={{ fontSize: 11, color: 'var(--color-muted-foreground)' }}>{String(check.checks_dropped)} older checks are on the record and not listed here.</span>
          )}
        </div>
      )}

      {props.signature === null || props.signature === undefined ? null : (
        <Section title="Signatures, by what ran each row" data="signatures">
          {props.signature}
        </Section>
      )}

      <Section title="This task" data="participation">
        <div data-cake-participation={participation === null ? 'unknown' : String(participation.value)} style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45 }}>
          Taking part in this task: {participation === null ? 'not read' : participation.value ? 'yes' : 'no'}
          {participation === null ? '' : `. ${participation.evidence}.`}
        </div>
      </Section>

      <ul data-cake-honesty="" style={{ margin: 0, paddingLeft: 16, display: 'flex', flexDirection: 'column', gap: 3 }}>
        {CAKE_HONESTY_LINES.map((line) => (
          <li key={line} style={{ fontSize: 11, color: 'var(--color-muted-foreground)', lineHeight: 1.4 }}>
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}
