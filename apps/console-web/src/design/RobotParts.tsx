// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ROBOT'S PIECES OF THE MAIN SCREEN: the retained-reading tag, the connection banner, the motion hold with its one
// guarded action, the one-line telemetry strip and the Cake panel's robot rows.
//
// Each is drawn in the imported design's own vocabulary (its chips, its washes, its kicker) and reads its facts from
// `model/robotStatus.ts` through `design/robot.ts`. None of them submits anything on render or on load: the only
// request here is the confirmation, and it is sent from a dialog the operator opened and checked, with the values the
// dialog displayed.

import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { number } from '../model/format';
import { confirmDialogOff, hostInstanceWords, sameHostInstance } from '../model/robotStatus';
import type { ConfirmShown, MotionPermissionView, RecoveryView, RobotLink, RobotStatus } from '../model/robotStatus';
import { tapView } from '../model/sources';
import { TELEMETRY_SILENT_AFTER_MS } from '../model/steps';
import type { ConsoleStore, StoreSnapshot } from '../store/store';
import { Dialog } from '../ui/Dialog';
import { ageWords } from '../ui/Age';
import { Button } from './Button';
import { capabilityRow, holdTone, hostRow, lastUpdateWords, residentRow } from './robot';
import type { RobotRow } from './robot';
import { C, INK, SURFACE, wash } from './theme';

const KICKER: CSSProperties = {
  fontSize: 12,
  color: 'var(--color-muted-foreground)',
};

const MONO: CSSProperties = { fontFamily: 'var(--font-mono), monospace', fontSize: 12 };

/** THE RETAINED-READING TAG. Every value kept from an earlier answer carries it, with the age that answer had. */
export function StaleTag(props: { ageMs?: number | null; title?: string }): ReactNode {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6, whiteSpace: 'nowrap' }} title={props.title}>
      <span
        style={{
          fontSize: 11, fontWeight: 700, letterSpacing: 0.4, padding: '1px 6px', borderRadius: 5,
          color: '#fde68a', background: wash(C.warn, 0.12), border: `1px solid ${wash(C.warn, 0.3)}`,
        }}
      >
        STALE
      </span>
      {props.ageMs === undefined || props.ageMs === null ? null : (
        <span style={{ fontSize: 12, color: C.warn }}>{ageWords(props.ageMs)} old</span>
      )}
    </span>
  );
}

/**
 * THE CONNECTION, WHEN THE ROBOT IS NOT ANSWERING. Drawn above the workspace, because every figure below it that
 * rests on the robot is now a retained one, and the operator has to know that before reading any of them.
 */
export function RobotConnectionBanner(props: { link: RobotLink }): ReactNode {
  const link = props.link;
  if (link.state !== 'not_answering' && link.state !== 'not_the_robot') return null;
  return (
    <div
      role="status"
      style={{
        display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', margin: '10px 0 0', padding: '10px 14px',
        borderRadius: 10, background: wash(C.bad, 0.08), border: `1px solid ${wash(C.bad, 0.35)}`, fontSize: 13,
      }}
    >
      <span style={{ fontWeight: 600, color: '#fecaca' }}>
        {link.label}: {link.state === 'not_the_robot' ? 'the resident answering does not report the pinned build.' : 'robot not answering.'}
      </span>
      <span style={{ color: INK.soft }}>{link.detail}.</span>
      <StaleTag ageMs={link.residentAgeMs} title="The age the retained resident reading had when the runtime last answered, on the runtime's clock." />
      {link.sinceReadMs === null ? null : (
        <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}>
          last answer from the runtime reached this tab {ageWords(link.sinceReadMs)} ago
        </span>
      )}
    </div>
  );
}

/**
 * THE MOTION HOLD, AND THE ONE ACTION THAT CAN LIFT IT.
 *
 * Shown whenever a robot resident gates motion here and the runtime does not report motion as permitted. It says WHY,
 * in the cause this console recognised and in the runtime's own sentence, and offers the confirmation only when the
 * runtime reports a RUNNING, IDENTIFIED host instance on a current reading, over the robot connection, of a resident
 * reporting the pinned build: never for pid 0, a held or DEGRADED supervisor slot, or another build. The build identity
 * identifies software; the connection and the session the operator confirms are what say which deployment it is.
 *
 * THE DIALOG POSTS EXACTLY WHAT IT DISPLAYED. The session and host instance are taken when it opens and shown; the
 * confirm button is off for every reason the banner button is off, re-evaluated on every reading, AND whenever the
 * current reading no longer shows what the dialog displays. A reading that changes under an open dialog also clears the
 * tick: the operator ticked for values they looked at, and those are not the values on the robot any more.
 *
 * Observe is not a motion command and is not gated by this; the banner says so, so an operator does not think the
 * whole console is off.
 */
export function MotionHold(props: {
  permission: MotionPermissionView;
  link: RobotLink;
  snapshot: StoreSnapshot;
  store: ConsoleStore;
  readOnlyReason: string | null;
}): ReactNode {
  const { permission, link, snapshot, store } = props;
  const [shown, setShown] = useState<ConfirmShown | null>(null);
  const [checked, setChecked] = useState(false);
  const off = props.readOnlyReason ?? (snapshot.confirming ? 'a confirmation from this tab is in flight; nothing is sent twice' : permission.confirmOff);
  const session = permission.currentSession;
  const instance = permission.hostInstance;
  // What the dialog displays no longer being what the runtime reports now is a reason of its own.
  const moved = shown !== null && (shown.session !== session || !sameHostInstance(shown.instance, instance));
  const dialogOff = confirmDialogOff(off, shown, permission);
  useEffect(() => {
    if (moved) setChecked(false);
  }, [moved]);
  if (!permission.applies || !permission.held) return null;
  const tone = holdTone(permission);
  const receipt = snapshot.lastConfirm;
  const close = (): void => {
    setShown(null);
    setChecked(false);
  };

  return (
    <div
      role="status"
      style={{
        display: 'flex', flexDirection: 'column', gap: 6, margin: '10px 0 0', padding: '12px 16px', borderRadius: 10,
        background: wash(tone, 0.08), border: `1px solid ${wash(tone, 0.35)}`, borderLeft: `3px solid ${tone}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={KICKER}>Robot motion</span>
        <span style={{ fontSize: 14, fontWeight: 600, color: INK.bright }}>Held</span>
        <span style={{ fontSize: 13, color: INK.soft }}>{permission.why}</span>
        {permission.code === null ? null : <span style={{ ...MONO, color: 'var(--color-muted-foreground)' }}>{permission.code}</span>}
      </div>
      {permission.changed === null ? null : (
        <div style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45 }}>What changed: {permission.changed}.</div>
      )}
      {permission.detail === null ? null : (
        <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45, textWrap: 'pretty' }}>The runtime: {permission.detail}.</div>
      )}
      <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.45 }}>
        Start, Scan scene, Return arm and Try again stay off until it is lifted. Observe does not move the robot and is not held by this.
        {permission.confirmedSession === null
          ? permission.changed !== null
            ? ' The last confirmation on this runtime was revoked by that change; a new one is needed.'
            : ' No session has been confirmed on this runtime.'
          : ` Last confirmed: session ${permission.confirmedSession}, host ${hostInstanceWords(permission.confirmedHostInstance)}${
              permission.confirmedWall === null ? '' : ` at ${permission.confirmedWall}`
            }.`}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', paddingTop: 2 }}>
        {off === null ? (
          <Button
            variant="default"
            data-control="robot-confirm"
            title="Check this is the robot you are connected to, then confirm its current resident session and host instance."
            onClick={() => {
              if (session === null || instance === null) return;
              setChecked(false);
              setShown({ session, instance });
            }}
          >
            Confirm this robot
          </Button>
        ) : null}
        {off === null ? (
          <span style={{ ...MONO, color: INK.quiet, overflowWrap: 'anywhere' }}>
            session {session ?? 'not reported'} · host {hostInstanceWords(instance)}
          </span>
        ) : (
          <span style={{ fontSize: 12, color: C.warn }}>Nothing to confirm: {off}.</span>
        )}
      </div>
      {receipt === null ? null : (
        <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>
          {receipt.kind === 'refused'
            ? `The last confirmation from this tab was refused: ${receipt.reason_code}. Nothing was confirmed; read the session and host above again and confirm what is shown now.`
            : receipt.kind === 'unknown'
              ? `The answer to the last confirmation could not be read: ${receipt.detail}. It was not sent again; the hold above is what the runtime reports now.`
              : receipt.ok
                ? 'The runtime took the last confirmation. Whether motion is permitted is what it reports above, read again after the request.'
                : `The runtime refused the last confirmation: ${receipt.reason_code ?? 'no reason was given'}. Read the session and host again and confirm what is shown now.`}
        </div>
      )}
      {shown !== null ? (
        <Dialog
          title="Confirm the connected robot"
          onClose={close}
          actions={
            <>
              <button type="button" className="button" onClick={close}>
                cancel
              </button>
              <button
                type="button"
                className="button button--primary"
                disabled={!checked || dialogOff !== null}
                title={dialogOff ?? undefined}
                onClick={() => {
                  if (!checked || dialogOff !== null) return;
                  const posted = shown;
                  close();
                  // EXACTLY WHAT THE DIALOG SHOWED. Not a fresh read: a value nobody looked at was not confirmed.
                  void store.confirmRobotSession(posted.session, posted.instance);
                }}
              >
                confirm this robot
              </button>
            </>
          }
        >
          <p>
            This grants permission to move for this resident session and this host instance only. A resident restart, a host
            restart (even one that reuses the pid), a re-created supervisor slot, or a resident that does not report the pinned
            build answering holds motion again.
          </p>
          <dl className="readings">
            <div className="readings__row">
              <dt>robot</dt>
              <dd>{link.label}</dd>
            </div>
            <div className="readings__row">
              <dt>resident session</dt>
              <dd style={{ ...MONO, wordBreak: 'break-all' }}>{shown.session}</dd>
            </div>
            <div className="readings__row">
              <dt>supervisor slot instance</dt>
              <dd style={{ ...MONO, wordBreak: 'break-all' }}>{shown.instance.slot_instance}</dd>
            </div>
            <div className="readings__row">
              <dt>host spawn sequence</dt>
              <dd style={MONO}>{String(shown.instance.spawn_sequence)}</dd>
            </div>
            <div className="readings__row">
              <dt>host pid on the robot</dt>
              <dd style={MONO}>{String(shown.instance.pid)}</dd>
            </div>
          </dl>
          {dialogOff === null ? null : <p style={{ color: C.warn, margin: '6px 0' }}>Confirm is off: {dialogOff}.</p>}
          <label className="check">
            <input type="checkbox" checked={checked} disabled={dialogOff !== null} onChange={(event) => setChecked(event.target.checked)} />
            <span>I have checked that this is the LeKiwi in front of me and that its host was started on purpose.</span>
          </label>
        </Dialog>
      ) : null}
    </div>
  );
}

/**
 * ONE LINE OF LIVE TELEMETRY: the arm and base from the continuous tap, with its rate and the age of its last line.
 *
 * When the tap is not connected the values are the word unavailable, never the last numbers: the rule `api/types.ts`
 * states for every source slot. The last recorded pose is in Connections, with its record time.
 */
export function TelemetryStrip(props: { snapshot: StoreSnapshot; nowMs: number; stale: boolean; simulated: boolean }): ReactNode {
  const telemetry = props.snapshot.telemetry;
  const tap = tapView(telemetry.tap, telemetry.tap_received_at_ms, props.nowMs, TELEMETRY_SILENT_AFTER_MS);
  const live = tap.live && !props.stale;
  const joints = live && tap.joints !== null ? Object.entries(tap.joints) : [];
  const base = live ? tap.base_velocity : null;
  return (
    <div
      style={{
        display: 'flex', alignItems: 'baseline', gap: 12, padding: '0 4px', flex: 'none', minHeight: 22, minWidth: 0,
        fontSize: 12, color: INK.quiet,
      }}
    >
      <span style={{ ...KICKER, flex: 'none' }}>Telemetry</span>
      {/* A SIMULATION runtime's tap is generated by it, like its frames, and is never read as the robot's. */}
      {props.simulated ? <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.4, color: C.warn }}>SIMULATED</span> : null}
      <span style={{ width: 6, height: 6, borderRadius: '50%', flex: 'none', alignSelf: 'center', background: live ? C.ok : INK.faint }} />
      {live ? (
        <>
          <span style={{ flex: 'none' }}>
            {tap.measured_rate_hz === null ? 'rate not measured' : `${tap.measured_rate_hz.toFixed(1)} Hz`}
            {tap.silent_for_ms === null ? '' : `, last line ${String(Math.round(tap.silent_for_ms))} ms ago`}
          </span>
          <span style={{ ...MONO, fontSize: 11, color: INK.soft, flex: 'none' }}>
            base {base === null ? 'unavailable' : `${number(base.x_mps, 2)} ${number(base.y_mps, 2)} m/s ${number(base.theta_dps, 1)} deg/s`}
          </span>
          <span
            title={joints.map(([name, value]) => `${name}: ${value === null ? 'not reported' : String(value)}`).join('\n')}
            style={{ ...MONO, fontSize: 11, color: INK.soft, minWidth: 0, flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            arm {joints.length === 0 ? 'no joints reported' : joints.map(([name, value]) => `${name.replace(/_/g, ' ')} ${value === null ? 'n/a' : number(value, 1)}`).join(' · ')}
          </span>
        </>
      ) : (
        <span>
          arm and base unavailable: {props.stale ? 'the robot is not answering' : tap.reason ?? tap.server_state ?? 'no telemetry line has reached this tab'}
          {tap.silent_for_ms === null ? '' : `, last line ${ageWords(tap.silent_for_ms)} ago`}
        </span>
      )}
    </div>
  );
}

function Row(props: { row: RobotRow }): ReactNode {
  const row = props.row;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: row.tone, flex: 'none' }} />
        <span style={{ fontSize: 13, color: INK.soft }}>{row.label}</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: row.tone }}>{row.value}</span>
        {row.stale ? <StaleTag ageMs={row.ageMs} /> : null}
      </div>
      {row.note === null ? null : (
        <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.45, overflowWrap: 'anywhere', paddingLeft: 15 }}>
          {row.note}
        </div>
      )}
    </div>
  );
}

/**
 * THE ROBOT'S CAKE SIDE, in the panel the design gave to Cake: its resident, its supervised host and the capability,
 * then the newest update and the restart reading. A runtime with no robot says that, and nothing else.
 */
export function RobotCakeRows(props: { status: RobotStatus | null; link: RobotLink; recovery: RecoveryView }): ReactNode {
  const status = props.status;
  if (status === null || !status.configured) {
    return (
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: INK.quiet, textWrap: 'pretty' }}>
        {props.link.state === 'not_read'
          ? `No robot reading: ${props.link.detail}.`
          : `No robot is configured on this runtime, so there is no robot resident, supervised host or capability to show. ${
              props.link.state === 'laptop_only' ? 'The resident it reads runs on this computer and is never shown as the robot’s.' : ''
            }`}
      </p>
    );
  }
  const update = lastUpdateWords(status);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Row row={residentRow(status, props.link)} />
      <Row row={hostRow(status, props.link)} />
      <Row row={capabilityRow(status, props.link)} />
      <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.45 }}>
        <span style={{ ...KICKER, fontSize: 10 }}>Last update </span>
        {update === null ? 'no update record in the resident’s flight ring' : update}
        {status.updates.stale ? ' ' : ''}
        {status.updates.stale ? <StaleTag ageMs={status.updates.observed?.age_ms ?? null} /> : null}
      </div>
      <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45, textWrap: 'pretty', padding: '8px 10px', borderRadius: 8, background: SURFACE.sunken }}>
        {/* A RETAINED READING'S RESTARTS ARE NOT NEWS ABOUT NOW: the box says it is stale, with that reading's age. */}
        {props.recovery.stale ? (
          <div style={{ marginBottom: 4 }}>
            <StaleTag ageMs={props.recovery.ageMs} title="These sentences come from the last reading of the robot, not a current one." />
          </div>
        ) : null}
        <div>{props.recovery.residentSentence}</div>
        <div style={{ marginTop: 4 }}>{props.recovery.componentSentence}</div>
      </div>
    </div>
  );
}
