// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE IMPORTED DESIGN, PORTED, WITH THE RUNNING CONSOLE UNDERNEATH IT.
//
// This is a React port of `design/imported/Cake Live Demo v2.dc.html`, pinned by the sha256 in `design/IMPORT.md`. The
// structure, the spacing, the type scale and the colours are the design's; everything they show comes from the
// existing reducer, store, API client, readiness helpers, command guards and result helpers, which this file does not
// reimplement and is not allowed to.
//
// THE PORT IS A PORT AND NOT A SYNC. `design/sync-check.sh` proves that every token the design references is defined;
// it renders nothing and reads no JSX, so a green check is NOT evidence that this file still matches the design. When
// a later revision is imported, `imported/` is replaced wholesale and the difference against this file is read and
// applied by hand, as a presentation diff. Nothing regenerates this file.
//
// WHAT THE DESIGN SHOWED THAT THIS SCREEN DOES NOT, each with the reason:
//
//   THE PROTOTYPE DOCK, which drove scenes and fixtures. It is prototype tooling. Its place in the layout is taken by
//   the one thing an operator needs in that corner, which is which runtime this is and how old its last snapshot is.
//
// TWO OF THE DESIGN'S ABSENCES HAVE SINCE BECOME REAL, and this header used to say they never would:
//
//   TWO CUBES. The design counts "n of 2 cubes placed" and carries a Cube A / Cube B target, and when this screen was
//   ported the task was one cube, counted by `model/mission.ts`. A runtime that carries a MISSION now serves one
//   (`GET /api/mission`), and where it does, the target, the count and the five-step rail all answer for the mission's
//   own active target: `design/steps5.ts` decides which target the rail is about and refuses to draw one target's
//   folded records under the other's name. A runtime with no mission is unchanged, down to the one-cube wording.
//   A CAKE CAPABILITY REQUEST, with candidates, an activation and a receipt. The design's version was illustrative and
//   there was no path to submit one. There is one now, and it is narrower than the drawing: the operator picks a KEY
//   out of the runtime's own prepared list and that key is the whole request. What comes back is a receipt; what says
//   a capability is actually there is the slot read back, which is the one activation state `design/cake.ts` will
//   draw as activated.
//
// WHAT THE DESIGN DID NOT HAVE AND THIS SCREEN MUST: the alert banners and the uncertain-command panel. A command
// whose execution is not known blocks every control on the screen, and it is drawn above everything for that reason.

import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useId, useState } from 'react';
import { resultPredicates } from '../model/acceptance';
import { commandScope } from '../model/command';
import { missionProgress, missionTarget } from '../model/mission';
import type { EvidenceState } from '../model/mission';
import {
  activationOffReason,
  missionPlaced,
  missionStartLabel,
  missionStartOff,
  missionStateWords,
  missionWaiting,
  targetStateShort,
  targetStateWords,
} from '../model/missionView';
import type { MissionWaiting } from '../model/missionView';
import type { MissionCandidate, MissionRecord } from '../model/missionRecord';
import { PHASE_STATUS_WORDS } from '../model/phase';
import { motionPermissionView, recoveryView, robotLink } from '../model/robotStatus';
import { staleStop } from '../model/reconnect';
import { missionRail } from './steps5';
import type { StepRow } from './steps5';
import { ATTACH_IS_DECIDED_ON_THE_ROBOT, ROBOT_ANSWER_STALE_AFTER_MS } from '../model/robot';
import { currentAction, readiness, resultForCurrentRun, runIsActive } from '../model/steps';
import { readinessOnRecord, readOnlyWords, robotBlock, startRefusal } from '../panels/commandGuards';
import { Actions, ATTESTATIONS, capabilityOffReason } from '../panels/Actions';
import { StripAlerts } from '../panels/StatusStrip';
import type { ScreenMode } from '../panels/StatusStrip';
import { UncertainPanel } from '../panels/UncertainPanel';
import { useConsole, useNowMs, useStore } from '../store/useConsole';
import type { StoreSnapshot } from '../store/store';
import { Dialog } from '../ui/Dialog';
import { StopBarHeight } from '../ui/stopBar';
import { ageWords } from '../ui/Age';
import { Button } from './Button';
import { selectedRun, liveFoldDescribes, needsHydration, viewForSelectedRun } from '../model/selectedRun';
import { actionCard, asSentence } from './actionCard';
import { ActivationEvidence } from './ActivationEvidence';
import { CapabilityCheckCard } from './CapabilityCheck';
import { SignatureCheckBlock } from './SignatureCheck';
import { checkChoice, checkUnavailableWords } from '../model/capabilityCheck';
import { activationLine } from './cake';
import { cameraPanel, cameraStreamBase } from './cameras';
import { resultRuleColour } from './resultRule';
import type { CameraPanelModel } from './cameras';
import { Drawer, DRAWER_NAME } from './Drawers';
import { heightRef } from './TopBar';
import type { ScreenOpening } from './DemoScreen';
import type { DrawerId } from './Drawers';
import { linkTone, robotChip } from './robot';
import { MotionHold, RobotCakeRows, RobotConnectionBanner, StaleTag, TelemetryStrip } from './RobotParts';
import { stopReceipt } from './receipts';
import type { Receipt } from './receipts';
import { pressStart } from './startPress';
import { timeline } from './timeline';
import { utcDayOf } from '../model/utcTime';
import { useStreamDecode } from './useStreamDecode';
import type { StreamBinding } from './useStreamDecode';
import { C, INK, SURFACE, TONE_DOT, TONE_INK, wash } from './theme';

const KICKER: CSSProperties = {
  fontSize: 11,
  letterSpacing: '.1em',
  textTransform: 'uppercase',
  color: 'var(--color-muted-foreground)',
};

const PLAIN_BUTTON: CSSProperties = {
  font: '500 12px var(--font-sans), sans-serif',
  whiteSpace: 'nowrap',
  flex: 'none',
  color: INK.quiet,
  background: 'transparent',
  border: '1px solid var(--color-border)',
  borderRadius: 6,
  padding: '3px 9px',
  cursor: 'pointer',
};

/* ---- the phase rail ------------------------------------------------------------------------------------------- */

/**
 * ONE STEP CHIP. The design's chip has four looks: past, current, waiting and not reached. This console's steps have
 * SIX statuses, because a step can be entered without being verified and can be failed, and those are different things
 * an operator acts on differently. The extra two get the design's own warn and bad colours.
 */
function phaseLook(row: StepRow): { bg: string; numBg: string; numFg: string; fg: string } {
  switch (row.status) {
    case 'active':
      return { bg: wash(C.accent, 0.1), numBg: C.accent, numFg: SURFACE.page, fg: INK.bright };
    case 'verified':
      return { bg: 'transparent', numBg: wash(C.ok, 0.16), numFg: C.ok, fg: INK.quiet };
    case 'failed':
      return { bg: wash(C.bad, 0.1), numBg: wash(C.bad, 0.22), numFg: C.bad, fg: '#fecaca' };
    case 'entered':
      return { bg: 'transparent', numBg: wash(C.warn, 0.18), numFg: C.warn, fg: INK.quiet };
    case 'not_observed':
      return { bg: 'transparent', numBg: wash(C.wait, 0.16), numFg: C.wait, fg: INK.faint };
    default:
      return { bg: 'transparent', numBg: 'var(--color-border)', numFg: INK.faint, fg: INK.faint };
  }
}

function PhaseRail(props: { rows: StepRow[] }): ReactNode {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 'none' }}>
      {props.rows.map((row, index) => {
        const look = phaseLook(row);
        return (
          <div key={row.step} style={{ display: 'flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap', flex: 'none' }}>
            <div
              title={`${row.label}: ${PHASE_STATUS_WORDS[row.status]}${row.basis.length === 0 ? '' : `. ${row.basis.join('; ')}`}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 12px 6px 7px',
                borderRadius: 8,
                background: look.bg,
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 20,
                  height: 20,
                  borderRadius: 6,
                  fontFamily: 'var(--font-mono), monospace',
                  fontSize: 12,
                  fontWeight: 600,
                  background: look.numBg,
                  color: look.numFg,
                }}
              >
                {index + 1}
              </span>
              <span style={{ fontSize: 13, fontWeight: 500, color: look.fg }}>{row.label}</span>
            </div>
            {index < props.rows.length - 1 ? (
              <span style={{ color: INK.rule, fontSize: 13, padding: '0 2px' }}>{'›'}</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/* ---- one camera ----------------------------------------------------------------------------------------------- */

/**
 * `awaiting` is the documented reason a camera is deliberately not streaming yet, as opposed to one that should be and
 * is not. A panel that said only "no line" would read as a fault and send somebody to look for a broken camera.
 *
 * THE FRONT CAMERA IS OFF BECAUSE ITS PREREQUISITE IS UNPROVEN, NOT BECAUSE IT WAS SHOWN TO CAUSE HARM, and this
 * screen used to say otherwise. The cover asserted that streaming both cameras lengthened the robot's observation
 * gaps, and the heading called the qualification failed. A controlled A/B/A refuted that: the both-streams window had
 * the SHORTEST longest gap (243.92 ms) and the worst was wrist-only (291.79 ms), so the criterion cannot carry a
 * verdict either way, and the measurement itself is now the suspect. The earlier bus-contention explanation is
 * withdrawn. That the front camera shares a hub with the servo adapter is RECORDED BUT IS NOT A FINDING, so it is not
 * offered here as the mechanism. `docs/autonomous-demo/camera-service.md` holds the record.
 */
function CameraCard(props: {
  panel: CameraPanelModel;
  /** The picture element of the current attempt, or null when no element is to be shown (`useStreamDecode`). */
  binding: StreamBinding | null;
  enlarged: boolean;
  awaiting?: string;
  onEnlarge: () => void;
}): ReactNode {
  const panel = props.panel;
  // The cover is drawn only where the panel has no picture; the front camera's qualification sentence takes its place.
  const cover =
    panel.cover === null || props.awaiting === undefined ? panel.cover : { title: 'Front off · qualification unresolved', note: props.awaiting };
  return (
    <div
      data-camera-panel={panel.role}
      style={{
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid var(--color-border)',
        borderRadius: 12,
        overflow: 'hidden',
        background: 'var(--color-card)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 12px', flex: 'none' }}>
        <span
          style={{
            fontFamily: 'var(--font-mono), monospace',
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: '.08em',
            whiteSpace: 'nowrap',
            textTransform: 'uppercase',
          }}
        >
          {panel.role}
        </span>
        <span
          data-camera-chip=""
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '2px 8px',
            borderRadius: 6,
            whiteSpace: 'nowrap',
            background: panel.chipBg,
          }}
        >
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: panel.tone }} />
          <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '.07em', color: panel.tone }}>{panel.word}</span>
        </span>
        <span
          data-camera-note=""
          title={panel.note}
          style={{
            fontSize: 12,
            color: 'var(--color-muted-foreground)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {panel.note}
        </span>
        <div style={{ flex: 1 }} />
        <button type="button" onClick={props.onEnlarge} style={PLAIN_BUTTON}>
          {props.enlarged ? 'Enlarged' : 'Enlarge'}
        </button>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: 'relative', background: SURFACE.well }}>
        {panel.streamUrl !== null ? (
          props.binding === null ? null : (
            /* ONE ELEMENT PER ATTEMPT. The key changes with the attempt, so a retry is a new element and a new request,
               and a picture an earlier attempt decoded can never stand for this one. Within an attempt nothing changes
               it: this is a stream, and replacing the element restarts it.
               THE WHOLE FRAME, NOT A CROP. The design asks for `fit="cover"`, which fills the panel by cutting the
               edges off. That is right for decorative media and wrong for a robot camera: the part it cuts is part of
               the scene the controller acted on, and somebody watching to see whether the cube is in view would be
               looking at a picture that silently is not all of it. */
            <img
              key={props.binding.key}
              ref={props.binding.ref}
              onLoad={props.binding.onLoad}
              onError={props.binding.onError}
              data-camera-stream=""
              src={panel.streamUrl}
              alt={`the ${panel.role} camera stream`}
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain' }}
            />
          )
        ) : panel.stillUrl !== null ? (
          <>
            <img
              data-camera-still=""
              src={panel.stillUrl}
              alt={`the newest stored frame from the ${panel.role} camera`}
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain' }}
            />
            {/* A STILL IS NEVER CALLED VIDEO, AND IT CARRIES ITS OWN AGE AND WHAT IT IS. The age beside it is the
                observation's own recording time, not the arrival of the last telemetry line. Only a LIVE runtime's
                saved frame is ever drawn here, and it is a retained reading, so it is marked STALE. A rehearsal's
                generated frames are in Evidence and never in a camera panel. */}
            <span
              style={{
                position: 'absolute',
                left: 8,
                bottom: 8,
                fontFamily: 'var(--font-mono), monospace',
                fontSize: 11,
                color: INK.soft,
                background: 'rgba(7,7,11,.82)',
                padding: '2px 7px',
                borderRadius: 4,
              }}
              title={panel.stillRecordedAt === null ? undefined : `recorded at ${panel.stillRecordedAt}`}
            >
              STALE · Saved observation
              {panel.stillRecordedAgeMs === null
                ? ', the record carries no time'
                : `, recorded ${ageWords(panel.stillRecordedAgeMs)} ago`}
            </span>
          </>
        ) : null}

        {cover === null ? null : (
          <div
            data-camera-cover=""
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(8,8,12,.72)',
              pointerEvents: 'none',
              padding: 16,
            }}
          >
            <div style={{ textAlign: 'center', maxWidth: 320 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: props.awaiting === undefined ? panel.tone : C.warn }}>{cover.title}</div>
              <div style={{ fontSize: 12, color: INK.quiet, marginTop: 3, lineHeight: 1.4 }}>{cover.note}</div>
            </div>
          </div>
        )}
      </div>

      {/* DELIVERY METADATA, SAID TO BE THAT. These are what the runtime handed this tab ABOUT the camera: frames it
          counted, when a new one was announced, when a line last arrived. None of them is the picture: whether the
          panel is showing video is the chip's, and the chip reads the decoded element. The three used to sit under a
          black panel reading like a healthy stream. Two different quantities, never added: what the RUNTIME measured,
          and when a line last reached HERE. */}
      <div
        data-camera-metadata=""
        style={{
          display: 'flex',
          gap: 12,
          justifyContent: 'space-between',
          padding: '6px 12px 8px',
          flex: 'none',
          fontFamily: 'var(--font-mono), monospace',
          fontSize: 11,
          color: 'var(--color-muted-foreground)',
        }}
      >
        <span
          title="Everything on this line is what the runtime told this tab about the camera. It is not the picture."
          style={{ ...KICKER, fontSize: 10, fontFamily: 'var(--font-sans), sans-serif', whiteSpace: 'nowrap' }}
        >
          Delivery metadata
        </span>
        {/* THREE DIFFERENT FACTS, NAMED SEPARATELY. The rate this tab counted from frames it was actually handed; the
            rate the RUNTIME measured over frames IT received, which is withheld once nothing has reached this tab
            inside the window; and the two ages, one for the last picture and one for the last line about this camera.
            The last two used to be one number, so a stalled camera read as "last line just now". */}
        <span title="Frames this tab was told about, counted here from each frame's own identity in the telemetry. Not frames this panel decoded.">
          {/* THREE STATES, NOT TWO. A rate, no frames at all, and a window that has not filled yet. The third used to
              print the second's answer's arithmetic over too few samples, which reads as a rate above the source cap. */}
          {panel.deliveredFps !== null
            ? `${panel.deliveredFps.toFixed(1)} fps delivered`
            : panel.measuringRate
              ? 'Measuring frame rate...'
              : 'no frames delivered'}
        </span>
        <span title="When the telemetry last announced a picture this tab had not already counted. Not the age of the picture itself.">
          {panel.lastFrameAgeMs === null ? 'no frame yet' : `last frame ${String(Math.round(panel.lastFrameAgeMs))} ms ago`}
        </span>
        <span title="The runtime answered about this camera. It does not mean a picture arrived.">
          {panel.lastLineAgeMs === null ? 'no line yet' : `last line ${String(Math.round(panel.lastLineAgeMs))} ms ago`}
        </span>
      </div>
    </div>
  );
}

/* ---- a mission that is held, and the one action that can release it --------------------------------------------- */

/**
 * WHAT THE OPERATOR SEES WHEN THE MISSION IS WAITING FOR A CAPABILITY, in the Cake panel's request slot.
 *
 * It sits in the Cake panel because that is where the imported design put the capability request, and the design review
 * returned it there: the request is a thing asked of the robot's resident, beside the rows that say what the resident
 * holds. It is offered ONLY while the mission's own record says it is waiting, and it exists to make a held mission read
 * as what it is. Three things it is careful about, each of which is the honest reading of a record rather than a
 * presentation choice:
 *
 *   A HELD MISSION IS NOT A CRASH. The sentence says why and names the target, and the line under it says nothing has
 *   failed and what puts it right. A blank screen, a spinner or a red banner would each say something the record does
 *   not.
 *
 *   THE ACTION SENDS A KEY. The chooser is the runtime's own candidate list and the button sends that candidate's key
 *   and nothing else. There is no field here for a path, a version or a file, because there is no such field on the
 *   request (`api/http.ts`).
 *
 *   A REQUEST IS NOT A VERDICT. What comes back from the button is a receipt for the request, said here. The verdict is
 *   the activation the RUNTIME recorded, with the slot read before and after it, which `ActivationReadback` draws from
 *   the mission record and which survives a reload of this tab.
 *
 * ONE OPERATION AT A TIME. While the newest recorded activation is SUBMITTED (in flight), the runtime refuses a second
 * one for this mission (`activation_in_flight`), so the button is off with that reason before anybody presses it. A
 * rejected, failed or completed-but-not-observed activation leaves the mission held and the button available again: a
 * new press is a new operation with a new identity, and the sentence under the verdict says so.
 */
export function CapabilityCard(props: {
  mission: MissionRecord;
  waiting: MissionWaiting;
  candidates: MissionCandidate[] | null;
  candidatesError: string | null;
  chosen: string | null;
  onChoose: (key: string) => void;
  activating: boolean;
  lastActivation: StoreSnapshot['lastActivation'];
  offReason: string | null;
  onActivate: () => void;
  onOpenHistory: () => void;
  /** True when the action card is not the one stating the hold, because a run is going on. */
  stateHold: boolean;
}): ReactNode {
  const candidates = props.candidates ?? [];
  const newest = props.mission.activations[props.mission.activations.length - 1] ?? null;
  const inFlight = newest !== null && newest.activation_state === 'SUBMITTED';
  const reasonId = useId();
  const off =
    props.offReason ??
    (props.activating
      ? 'an activation from this tab is in flight; nothing is sent twice'
      : inFlight
        ? `operation ${newest.operation_uuid} is in flight (SUBMITTED), and the runtime refuses a second activation for this mission until it is answered; Stop ends the mission`
        : candidates.length === 0
          ? 'the runtime has offered no prepared candidate to activate'
          : props.chosen === null
            ? 'choose one of the prepared candidates first'
            : null);

  return (
    <div
      data-capability-request=""
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
        padding: '12px 14px',
        border: '1px solid var(--color-border)',
        borderLeft: `3px solid ${C.wait}`,
        borderRadius: 10,
        background: SURFACE.raised,
        flex: 'none',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        {/* THE HOLD IS STATED ONCE: in the action card, unless a run is going on and the action card is saying what that
            run is doing, in which case it is stated here. This card is the request that releases it. */}
        <span style={KICKER}>Capability request</span>
        {props.stateHold ? <span style={{ fontSize: 14, fontWeight: 600, color: '#ddd6fe' }}>{props.waiting.headline}</span> : null}
        {props.waiting.reasonCode === null ? null : (
          <span style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 11, color: 'var(--color-muted-foreground)' }}>
            {props.waiting.reasonCode}
          </span>
        )}
      </div>
      <div style={{ fontSize: 12, color: INK.soft, lineHeight: 1.45, textWrap: 'pretty' }}>{props.waiting.sentence}</div>
      <div style={{ fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.45, textWrap: 'pretty' }}>
        {props.waiting.recovery}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {/* THE PREPARED CANDIDATES, BY KEY. The digest is shown beside the key because it is what identifies the thing
            the key stands for; it is never sent, and neither is anything else but the key. */}
        <select
          data-control="candidate"
          aria-label="prepared candidate"
          value={props.chosen ?? ''}
          onChange={(event) => props.onChoose(event.target.value)}
          disabled={candidates.length === 0 || props.activating}
          aria-disabled={candidates.length === 0 || props.activating ? 'true' : 'false'}
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
          {candidates.length === 0 ? (
            <option value="">no prepared candidate</option>
          ) : (
            <option value="" disabled>
              choose a prepared candidate
            </option>
          )}
          {candidates.map((candidate) => (
            <option key={candidate.key} value={candidate.key}>
              {candidate.key}
            </option>
          ))}
        </select>
        <Button
          variant="default"
          data-control="activate"
          disabled={off !== null}
          aria-disabled={off !== null ? 'true' : 'false'}
          aria-describedby={off === null ? undefined : reasonId}
          onClick={props.onActivate}
          title={off ?? 'Send this candidate key to the runtime for activation.'}
        >
          {props.activating ? 'Activating' : 'Activate this candidate'}
        </Button>
        <button type="button" onClick={props.onOpenHistory} style={PLAIN_BUTTON}>
          Activation history
        </button>
      </div>
      {props.chosen === null ? null : (
        <div style={{ fontSize: 11, color: 'var(--color-muted-foreground)', overflowWrap: 'anywhere' }}>
          digest {candidates.find((item) => item.key === props.chosen)?.payload_digest ?? 'not reported'}
        </div>
      )}
      {/* WHY THE BUTTON IS OFF, where it can be read without hovering. */}
      {off === null ? null : (
        <div id={reasonId} data-control-reason="activate" style={{ fontSize: 12, color: C.warn, lineHeight: 1.45, textWrap: 'pretty' }}>
          Off: {off}.
        </div>
      )}
      {props.candidatesError === null ? null : (
        <div style={{ fontSize: 12, color: C.warn }}>
          The prepared candidates could not be read: {props.candidatesError}. That is a read that failed, not an empty list.
        </div>
      )}

      {/* THE RECEIPT FOR THE REQUEST, kept apart from the verdict below it in words as well as in place. */}
      {props.activating ? (
        <div style={{ fontSize: 12, color: C.wait, lineHeight: 1.45 }}>
          The request is in flight: this tab is waiting for the runtime&apos;s answer, and nothing is sent a second time.
        </div>
      ) : props.lastActivation === null ? null : (
        <div data-activation-receipt="" style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45, textWrap: 'pretty' }}>
          {activationReceiptWords(props.lastActivation)}
        </div>
      )}
    </div>
  );
}

/** The receipt for the last activation request this tab sent, in words. It is never the verdict. */
function activationReceiptWords(receipt: NonNullable<StoreSnapshot['lastActivation']>): string {
  if (receipt.kind === 'refused') {
    if (receipt.reason_code === 'activation_in_flight') {
      return 'The runtime refused the last request from this tab because an earlier operation for this mission is still in flight. Nothing new was submitted.';
    }
    if (receipt.reason_code === 'mission_stopping') {
      return "The runtime refused the last request from this tab because the mission's stop has been asked for. Nothing was submitted.";
    }
    return `The last request from this tab did not land: ${receipt.reason_code}. Nothing was judged and no transaction was opened.`;
  }
  if (receipt.kind === 'unknown') {
    return `The answer to the last request from this tab could not be read: ${receipt.detail}. Whether it reached the resident is not known from here, nothing was sent again, and the record below is what the runtime holds.`;
  }
  return `The runtime took the last request from this tab and answered ${receipt.activation_state}${
    receipt.transaction_phase === null ? '' : `, transaction phase ${receipt.transaction_phase}`
  }. That is a receipt for the request; what it came to is the record below.`;
}

/**
 * WHAT CHANGED IN THIS MISSION: the newest activation the runtime recorded, with the slot as it read BEFORE the submit and
 * AFTER the answer. Every earlier one is kept in Evidence, drawn by the same component (`design/ActivationEvidence.tsx`),
 * and this says so when there are any.
 *
 * The verdict is `activationLine`'s, the one place that decides which state reads as activated. Beside it, the evidence
 * the verdict came from: the transaction's phase and the resident's handle for it, and the capability slot's lifecycle
 * state, capsule digest, generation and instance on both sides of the operation. A capsule and a generation that moved
 * are what an activation looks like in the slot; a verdict with nothing under it would ask to be taken on trust.
 *
 * A read that failed is said to have failed. It is never drawn as an empty slot, and an absent value is "not reported",
 * never a zero.
 */
export function ActivationReadback(props: { mission: MissionRecord; candidates?: readonly MissionCandidate[] | null }): ReactNode {
  const newest = props.mission.activations[props.mission.activations.length - 1] ?? null;
  if (newest === null) {
    return <p style={{ margin: 0, fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.5 }}>No update in this mission.</p>;
  }
  const line = activationLine(newest, props.candidates ?? null);
  const earlier = props.mission.activations.length - 1;
  return (
    <div data-activation-readback="" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: line.tone, flex: 'none' }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: line.tone }}>{line.headline}</span>
        <span style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 11, color: 'var(--color-muted-foreground)' }}>
          {newest.candidate_key ?? 'candidate not named'}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 12, color: INK.quiet, lineHeight: 1.5, textWrap: 'pretty' }}>{line.sentence}</p>
      {line.qualifier === null ? null : <p style={{ margin: 0, fontSize: 12, color: C.warn, lineHeight: 1.5, textWrap: 'pretty' }}>{line.qualifier}</p>}
      <ActivationEvidence activation={newest} />
      {earlier === 0 ? null : (
        <p style={{ margin: 0, fontSize: 11, color: 'var(--color-muted-foreground)', lineHeight: 1.5 }}>
          {earlier === 1
            ? 'The earlier activation of this mission, with its slot read-back, is in Evidence.'
            : `The ${String(earlier)} earlier activations of this mission, each with its slot read-back, are in Evidence.`}
        </p>
      )}
    </div>
  );
}

/* ---- the screen ----------------------------------------------------------------------------------------------- */

/**
 * THE CAKE COLUMN: the robot's resident, its supervised host and the capability, the request slot (a held mission's
 * activation, or the capability slot check and the signature check while no mission is active) and what changed in the
 * mission. It is the classic screen's right-hand column, moved into its own component unchanged so the simplified
 * screen mounts the same panel with the same guards.
 */
export function CakeColumn(props: {
  snapshot: StoreSnapshot;
  store: ReturnType<typeof useStore>;
  nowMs: number;
  /** Why nothing on this page may command, or null. */
  readOnlyReason: string | null;
  running: boolean;
  onOpenDrawer: (id: DrawerId) => void;
}): ReactNode {
  const { snapshot, store, nowMs } = props;
  const view = snapshot.view;
  const running = props.running;
  /** The candidate key the operator picked. Only a key is ever held here, and only a key is ever sent. */
  const [candidate, setCandidate] = useState<string | null>(null);
  /** The prepared Plan picked for a capability slot check. A key; the digest and the session come from the runtime's read. */
  const [checkCandidate, setCheckCandidate] = useState<string | null>(null);
  const robotRead = { status: snapshot.robot, error: snapshot.robotError, readAtMs: snapshot.robotReadAtMs };
  const link = robotLink(robotRead, nowMs);
  const recovery = recoveryView(snapshot.robot, link);
  const chip = robotChip(link);
  const mission = snapshot.mission;
  const waiting = missionWaiting(mission, snapshot.robot?.configured === true ? snapshot.robot.capability.name : null);
  const readOnly = props.readOnlyReason !== null;
  const readOnlyReason = props.readOnlyReason ?? '';

  /* --- the prepared activation, offered only while the mission says it is waiting ------------------------------- */

  const candidates = snapshot.missionCandidates;
  // NOTHING IS PRESELECTED. The keys arrive sorted, and the first of them was the abort-swap candidate, which one press
  // sent. A key the operator did not choose, or one the runtime no longer offers, is no choice at all.
  const chosen = candidate !== null && (candidates ?? []).some((item) => item.key === candidate) ? candidate : null;
  const activateOff = props.readOnlyReason ?? activationOffReason(mission);
  const onActivate = (): void => {
    if (chosen === null) return;
    // ONE KEY. The store refuses a second request while one is in flight and nothing is ever resent.
    void store.activateCandidate(chosen);
  };

  /* --- the capability slot check, offered only while no mission is active ------------------------------------------ */

  // A MISSION THAT HAS NOT ENDED OWNS THE REQUEST SLOT. The check is the capability card's other mode, drawn only when
  // there is no mission or the last one has ended; the runtime refuses a check under a carried mission anyway.
  const missionActive = mission !== null && mission.state !== 'COMPLETE' && mission.state !== 'STOPPED' && mission.state !== 'FAILED';
  const check = snapshot.capabilityCheck;
  const onApplyCheck = (): void => {
    if (check === null) return;
    const key = checkChoice(check, checkCandidate);
    const digest = check.candidates.find((item) => item.key === key)?.payload_digest ?? null;
    const session = check.current.session_uuid;
    if (key === null || digest === null || session === null) return;
    // THE SESSION OF THE CURRENT READING, the one printed beside the button, sent as it is. The store refuses a second
    // press while one is in flight, and the runtime refuses the session if the robot reports another one now.
    void store.applyCapabilityCheck(key, digest, session);
  };

  return (
    <aside
      style={{
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        border: '1px solid var(--color-border)',
        borderRadius: 12,
        background: 'var(--color-card)',
        overflow: 'hidden',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px 12px', flex: 'none' }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Cake</span>
        <div style={{ flex: 1 }} />
        {/* BOUND TO THE ROBOT'S RESIDENT (GET /api/robot), not to the snapshot's synthetic ladder (finding F3). */}
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 7,
            padding: '4px 10px',
            borderRadius: 7,
            whiteSpace: 'nowrap',
            background: chip.bg,
            border: `1px solid ${chip.border}`,
          }}
        >
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: chip.dot }} />
          <span style={{ fontSize: 12, fontWeight: 600, color: chip.fg }}>{chip.label}</span>
        </span>
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '0 16px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        {/* THE ROBOT'S RESIDENT, ITS SUPERVISED HOST AND THE CAPABILITY, with the newest update and whether a
            component recovered or the whole resident restarted. Identities, digests and counters are in
            Connections. Nothing here is the laptop's resident, and nothing retained is drawn as current. */}
        <RobotCakeRows status={snapshot.robot} link={link} recovery={recovery} />
        {/* --- the request slot: the mission is held, and the one action that can release it --- */}
        {/* THE REQUEST SLOT'S OTHER MODE: a capability slot check, only while no mission is active. */}
        {missionActive || waiting !== null ? null : check !== null ? (
          <CapabilityCheckCard
            state={check}
            readAtMs={snapshot.capabilityCheckReadAtMs}
            nowMs={nowMs}
            readError={snapshot.capabilityCheckError}
            chosen={checkCandidate}
            onChoose={setCheckCandidate}
            applying={snapshot.checking}
            lastCheck={snapshot.lastCheck}
            readOnlyReason={readOnly ? readOnlyReason : null}
            onApply={onApplyCheck}
          />
        ) : view.data_mode === 'live' && snapshot.capabilityCheckError !== null ? (
          <div data-capability-check-off="" style={{ fontSize: 12, color: 'var(--color-muted-foreground)', lineHeight: 1.45, textWrap: 'pretty' }}>
            Capability slot check (no motion): not offered here, because {checkUnavailableWords(snapshot.capabilityCheckError, snapshot.capabilityCheckErrorDetail)}.
          </div>
        ) : null}
        {/* DIRECTLY UNDER THE SLOT CHECK: the signature policy check, the release verifier run on the robot, no motion. */}
        {missionActive || waiting !== null ? null : (
          <SignatureCheckBlock
            state={snapshot.signatureCheck}
            readError={snapshot.signatureCheckError}
            readErrorDetail={snapshot.signatureCheckErrorDetail}
            live={view.data_mode === 'live'}
            running={snapshot.signatureChecking}
            lastRun={snapshot.lastSignatureCheck}
            readOnlyReason={readOnly ? readOnlyReason : null}
            onRun={() => void store.runSignatureCheck()}
          />
        )}
        {/* ONLY A HOLD FOR THE CAPABILITY offers the activation. The runtime also holds a mission after a failed
            attempt, for the operator's decision, and admits no activation for it. */}
        {mission === null || waiting === null || waiting.kind !== 'capability' ? null : (
          <CapabilityCard
            mission={mission}
            waiting={waiting}
            candidates={candidates}
            candidatesError={snapshot.missionCandidatesError}
            chosen={chosen}
            onChoose={setCandidate}
            activating={snapshot.activating}
            lastActivation={snapshot.lastActivation}
            offReason={activateOff}
            onActivate={onActivate}
            onOpenHistory={() => props.onOpenDrawer('history')}
            stateHold={running}
          />
        )}
        {mission === null ? null : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 10, borderTop: '1px solid var(--color-border)' }}>
            <span style={KICKER}>What changed in this mission</span>
            <ActivationReadback mission={mission} candidates={snapshot.missionCandidates} />
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => props.onOpenDrawer('connections')} style={{ ...PLAIN_BUTTON, padding: '5px 11px' }}>
            Connections
          </button>
          <button type="button" onClick={() => props.onOpenDrawer('logs')} style={{ ...PLAIN_BUTTON, padding: '5px 11px' }}>
            Update records
          </button>
        </div>
      </div>
    </aside>
  );
}

/** The screen's title follows the runtime's mode: "Live demonstration" is said only of a live runtime. */
export function screenTitle(mode: ScreenMode, sourceKind: 'live' | 'synthetic'): string {
  if (mode === 'replay') return 'Recorded run';
  if (sourceKind === 'synthetic' || mode === 'synthetic') return 'Rehearsal';
  return 'Live demonstration';
}

export function Screen(props: { opening?: ScreenOpening }): ReactNode {
  const store = useStore();
  const snapshot = useConsole();
  const nowMs = useNowMs();
  const opening = props.opening ?? {};

  const [drawer, setDrawer] = useState<DrawerId | null>(opening.drawer ?? null);
  // The header's height: the drawer and every dialog start below it, so the only Stop is never covered (refix round 9).
  const [headerHeight, setHeaderHeight] = useState(opening.barHeight ?? 0);
  const [enlarged, setEnlarged] = useState<string>('wrist');
  const [stopBusy, setStopBusy] = useState(false);
  /**
   * The receipt for the last Start or Stop pressed in this tab: what the runtime said, or why nothing was sent. It is
   * kept until dismissed or replaced, because a press that answers with silence reads as a press that worked.
   */
  const [receipt, setReceipt] = useState<(Receipt & { what: 'Start' | 'Stop' }) | null>(null);
  const [attesting, setAttesting] = useState(opening.attest !== undefined);
  const [checked, setChecked] = useState<boolean[]>(ATTESTATIONS.map(() => opening.attest?.allChecked ?? false));

  useEffect(() => {
    void store.start();
    return () => store.dispose();
  }, [store]);

  // READ THE SELECTED RUN'S OWN RECORDS when this tab holds none of them. Guarded inside the store against a stale
  // answer replacing a newer selection, and never repeated for a run already read: a finished run's records do not
  // change. It fetches; it commands nothing.
  const selectedForHydration = selectedRun(snapshot.view, snapshot.session.runtime_replaced);
  const hydrateId = needsHydration(selectedForHydration, snapshot.view, snapshot.hydrated) ? selectedForHydration.run_id : null;
  useEffect(() => {
    if (hydrateId === null) return;
    void store.hydrateRun(hydrateId);
  }, [store, hydrateId]);

  // Escape closes whatever is open, which is the one key somebody reaches for over a robot that is moving.
  useEffect(() => {
    if (drawer === null && !attesting) return undefined;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      setAttesting(false);
      setDrawer(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawer, attesting]);

  const view = snapshot.view;
  const replaying = snapshot.replay !== null;
  const mode: ScreenMode = replaying ? 'replay' : view.data_mode;
  const ready = readinessOnRecord(readiness(view, nowMs, replaying), view, snapshot.mission);
  const scope = commandScope(view, (runId) => {
    const history = snapshot.history;
    if (history === null || !history.ok) return null;
    return history.entries.find((item) => item.run_id === runId)?.command_type ?? null;
  });

  // WHICH ROBOT, AND HOW CURRENT WHAT IS KNOWN ABOUT IT IS. Read from GET /api/robot and from nothing else: the
  // desktop runtime's session id is not a robot, and the `/api/cake` resident does not say which machine answered.
  const robotRead = { status: snapshot.robot, error: snapshot.robotError, readAtMs: snapshot.robotReadAtMs };
  const link = robotLink(robotRead, nowMs);
  const permission = motionPermissionView(robotRead, nowMs);
  const robotStale = link.stale;
  const snapshotAgeMs = view.snapshot_at_ms === null ? null : Math.max(0, nowMs - view.snapshot_at_ms);
  const snapshotStale = snapshot.sourceError !== null || (snapshotAgeMs !== null && snapshotAgeMs > ROBOT_ANSWER_STALE_AFTER_MS);

  const action = currentAction(view, ready.ready, scope);
  // ONE RUN FOR THE WHOLE PRESENTATION. Target and progress answer for `selected`; command eligibility does NOT and is
  // decided by `ready`, `scope` and the guards, which read the live snapshot.
  const selected = selectedRun(view, snapshot.session.runtime_replaced);
  // THE RAIL ANSWERS FOR THE MISSION'S ACTIVE TARGET when there is a mission, and is the plain live fold when there is
  // not. `missionRail` is what refuses to draw one target's run under the other target's name.
  const mission = snapshot.mission;
  const rail = missionRail(mission, view, selected.run_id);
  const waiting = missionWaiting(mission, snapshot.robot?.configured === true ? snapshot.robot.capability.name : null);
  const runView = viewForSelectedRun(view, selected, snapshot.hydrated);
  const running = runIsActive(view);
  const missionRetained = mission !== null && snapshot.missionError !== null;
  const missionAgeMs = snapshot.missionReadAtMs === null ? null : Math.max(0, nowMs - snapshot.missionReadAtMs);

  // THE PICTURE ELEMENTS, WATCHED. Each camera's LIVE rests on what its own element decoded in this tab, attempt by
  // attempt; the telemetry's frame stamps are passed only so a stream that ended can be told from a camera that stopped.
  const frameAgeOf = (role: string): number | null => {
    const at = snapshot.telemetry.preview_frame_at_ms_by_role[role] ?? null;
    return at === null ? null : Math.max(0, nowMs - at);
  };
  const wristStream = useStreamDecode(cameraStreamBase(snapshot, 'wrist', store.currentSource, nowMs), frameAgeOf('wrist'));
  const frontStream = useStreamDecode(cameraStreamBase(snapshot, 'front', store.currentSource, nowMs), frameAgeOf('front'));
  const wrist = cameraPanel(snapshot, 'wrist', store.currentSource, nowMs, wristStream.decode);
  const front = cameraPanel(snapshot, 'front', store.currentSource, nowMs, frontStream.decode);

  const result = resultForCurrentRun(view);
  // THE RUN'S OWN PREDICATES, for the single-cube progress figure only. The outcome itself is History's.
  const resolved = result === null ? view.predicates : resultPredicates(view.predicates, result.run_id, snapshot.resultDetail);
  const evidenceState: EvidenceState =
    result === null || view.predicates.some((item) => item.predicate === 'clearance_target_met')
      ? 'retained'
      : snapshot.resultDetail === null
        ? 'reading'
        : snapshot.resultDetail.state;
  const target = missionTarget(runView, resolved, liveFoldDescribes(selected) || snapshot.hydrated?.run_id === selected.run_id);
  const progress = missionProgress(view, resolved, evidenceState, scope.requires_placement);
  // A FIXTURE RESULT IS NEVER A PLACEMENT. With no mission the single-cube figure says "placed" only for a live runtime.
  const progressWords =
    mission !== null
      ? missionPlaced(mission).short
      : view.data_mode === 'live' && !replaying
        ? progress.words
        : // A replay's recording does not say here that it was a live runtime's, so it never says "placed" either.
          `${replaying ? 'Replay' : 'Rehearsal'}: ${progress.words.replace(/placed/g, 'recorded done')}`;

  const lines = timeline(view.recent_events, utcDayOf(mission?.started_wall_utc ?? null) ?? new Date().toISOString().slice(0, 10));
  const latest = lines[lines.length - 1] ?? null;

  /* --- Start and Stop, using the SAME guards as the controls row --------------------------------------------- */

  const readOnly = replaying || !view.contract_compatible || !view.commands_enabled;
  const readOnlyReason = readOnlyWords(view, replaying, undefined, snapshot.handshake.state !== 'not_answered') ?? '';

  // START IS THE CANONICAL MISSION START (POST /api/mission/start) AND NOTHING ELSE. It is off whenever the runtime
  // would certainly refuse it, first for a reason about missions (no mission surface, which is what a LIVE runtime
  // answers until the robot-side execution adapter exists; a mission already carried; a run in the slot), then for the
  // same command guards every control on this screen makes: the uncertain block, the capability list, the lease, the
  // robot's motion hold and a start still unanswered.
  // This screen draws no Try again control, so the decision hold's sentence points to the screen that does.
  const startReading = { mission, missionError: snapshot.missionError, missionErrorDetail: snapshot.missionErrorDetail, runActive: running, retryOnThisScreen: false };
  const startOff = readOnly
    ? readOnlyReason
    : missionStartOff(startReading) ??
      capabilityOffReason(view, 'start_cycle', mission) ??
      robotBlock(view, nowMs) ??
      startRefusal(snapshot.commands);
  const startBusy = snapshot.startingMission;
  const startLabel = missionStartLabel(startReading, startBusy);
  const startDisabled = startOff !== null || startBusy;

  // THE START DIALOG CLOSES WHEN THE RECORD SHOWS A RUN (refix round 9, the class of verify-r8 M1): nothing is left for it
  // to start, and it must not stand between the operator and Stop. Not drawn from the render the run shows up in; the
  // press is forgotten, so it does not come back when the run ends.
  useEffect(() => {
    if (running) setAttesting(false);
  }, [running]);

  const runStart = (): void => {
    setReceipt(null);
    // ONE PRESS, ONE MISSION START (`design/startPress.ts`): the guards again at the click, then POST /api/mission/start.
    void pressStart(store).then((answer) => setReceipt({ what: 'Start', ...answer }));
  };

  const onStartPressed = (): void => {
    // A live runtime asks for the physical checks once, in a dialog. A simulation has nothing to attest about.
    if (view.data_mode !== 'synthetic') {
      setChecked(ATTESTATIONS.map(() => false));
      setAttesting(true);
      return;
    }
    runStart();
  };

  // A STALE TAB'S STOP takes over once on its press (round 7 item 3); "Reload to stop" only after a takeover that could not send.
  const stopStale = staleStop(snapshot.session, snapshot.lastStop);
  const stopReloadWords = stopStale !== null && stopStale.reload ? stopStale.title : null;
  const reloadPage = (): void => {
    if (typeof window !== 'undefined') window.location.reload();
  };

  // THE UNIFIED STOP. POST /api/stop, which the runtime answers by stopping the controller run through its guarded path
  // and then the mission; it never signals the robot host or the resident. What the runtime said it stopped is shown as a
  // receipt, and whether motion ceased is left to the run's own stop records.
  const onStop = (): void => {
    setStopBusy(true);
    void store
      .requestStop()
      .then((ack) => setReceipt({ what: 'Stop', ...stopReceipt(ack) }))
      .finally(() => setStopBusy(false));
  };

  /* --- what the robot is doing, said once ------------------------------------------------------------------------- */

  // THE ACTION CARD IS THE ONE PLACE THAT SAYS WHAT THE ROBOT IS DOING, one sentence per fact (`design/actionCard.ts`).
  // A robot that is not answering is said to be not answering, never described by its last action. A finished run's
  // outcome is History's, not this card's.
  const card = actionCard({ robotStale, link, waiting, running, result, ready, action });
  const ruleColour = robotStale ? C.bad : waiting !== null && !running ? C.wait : resultRuleColour({ result: null, running, ready: ready.ready, blocked: startOff !== null });

  const title = screenTitle(mode, snapshot.sourceKind);
  const noRobotRehearsal = (view.data_mode === 'synthetic' || snapshot.sourceKind === 'synthetic') && link.state !== 'answering' && link.state !== 'not_answering' && link.state !== 'not_the_robot';
  const identity = {
    dot: linkTone(link),
    label: noRobotRehearsal ? 'No robot: laptop rehearsal' : link.label,
    state: noRobotRehearsal ? null : link.headline,
  };

  const camCols = enlarged === 'front' ? 'minmax(0, 1fr) minmax(0, 1.55fr)' : 'minmax(0, 1.55fr) minmax(0, 1fr)';

  return (
    // THE HEADER'S HEIGHT, handed to every dialog under this screen: none covers the only Stop (`ui/stopBar.ts`).
    <StopBarHeight value={headerHeight}>
      <div
        className="dc"
        style={{
          height: '100vh',
          minHeight: 700,
          display: 'flex',
          flexDirection: 'column',
          // CLIP, NOT HIDDEN (refix round 9): `hidden` made this root a scroll container that never scrolls, so the sticky
          // header below stuck to it and scrolled away with the page below the 700 px minimum height, Stop with it.
          overflow: 'clip',
          background: SURFACE.page,
          color: INK.body,
          fontFamily: 'var(--font-sans), system-ui, sans-serif',
          WebkitFontSmoothing: 'antialiased',
        }}
      >
        {/* --- header: which robot, whether it answers, and the two controls that are always reachable --- */}
        {/* THE HEADER REPORTS ITS HEIGHT, and is drawn above the drawer and every dialog, which start below it (refix round
            9): the Diagnostics drawer covered the whole view, this header and its only Stop included. */}
        <header
          ref={heightRef(setHeaderHeight)}
          data-region="classic-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            // WRAPS AT A NARROW VIEW (refix round 9): on one line the controls sat past the right edge at 390 px, Stop at
            // x 618, clipped by the page's overflow.
            flexWrap: 'wrap',
            gap: 20,
            minHeight: 58,
            padding: '0 20px',
            borderBottom: '1px solid var(--color-border)',
            flex: 'none',
            // STICKY: below the screen's 700 px minimum height the page scrolls, and the only Stop must stay in view.
            position: 'sticky',
            top: 0,
            zIndex: 20,
            background: SURFACE.page,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 11, whiteSpace: 'nowrap', flex: 'none' }}>
            <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: '-.02em' }}>shinro</span>
            <span style={{ width: 4, height: 4, borderRadius: '50%', background: C.accent }} />
            <span style={{ fontSize: 13, color: INK.quiet }}>{title}</span>
          </div>

          {/* WHICH LEKIWI. The robot's own display label when the runtime reports one, and otherwise the sentence for
              its absence. It never falls back to the desktop runtime's session id, which names this laptop's runtime. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }} title={link.detail}>
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                flex: 'none',
                background: identity.dot,
                animation: running && !robotStale ? 'softpulse 2.2s ease-in-out infinite' : undefined,
              }}
            />
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontStyle: link.labelKnown || noRobotRehearsal ? undefined : 'italic',
              }}
            >
              {identity.label}
            </span>
            {identity.state === null ? null : (
              <span style={{ fontSize: 12, color: robotStale ? C.warn : 'var(--color-muted-foreground)', whiteSpace: 'nowrap', flex: 'none' }}>
                {identity.state}
              </span>
            )}
            {robotStale ? <StaleTag ageMs={link.residentAgeMs} /> : null}
          </div>

          <div style={{ flex: 1 }} />

          <div data-region="classic-controls" style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, flex: '0 1 auto', minWidth: 0, maxWidth: '100%', padding: '8px 0' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', flexWrap: 'wrap', gap: 14 }}>
              <span style={{ fontSize: 12, color: 'var(--color-muted-foreground)', maxWidth: 210, lineHeight: 1.35 }}>
                Stop ends the program in software; it is not an emergency stop.
              </span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, whiteSpace: 'nowrap' }}>
                {/* START BEGINS THE MISSION, through the canonical mission start and nothing else. Off means off: the native
                    attribute, so the browser refuses the click and the key, and aria-disabled, so assistive technology says
                    so too. The reason is on the screen under the buttons, not only in a tooltip. */}
                <Button
                  variant={startLabel === 'Start' || startLabel === 'Starting' ? 'default' : 'secondary'}
                  data-control="start"
                  disabled={startDisabled}
                  aria-disabled={startDisabled ? 'true' : 'false'}
                  aria-describedby={startOff === null ? undefined : 'start-off-reason'}
                  onClick={onStartPressed}
                  title={startOff === null ? 'Start the mission: the runtime carries its targets one after the other.' : asSentence(startOff)}
                >
                  {startLabel}
                </Button>
                {/* A TAB WHOSE CREDENTIAL THE CONSOLE SERVER NO LONGER ACCEPTS: the plain Stop, whose press takes over once
                    (`model/reconnect.staleStop`, round 7 item 3); "Reload to stop", whose press reloads the page, only after a
                    press whose takeover could not send (verify-r1 must_fix 1). */}
                {stopReloadWords !== null ? (
                  <Button variant="outline" data-control="stop" data-stop-reload="" aria-disabled="false" onClick={reloadPage} title={stopReloadWords}>
                    Reload to stop
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    data-control="stop"
                    data-stop-takeover={stopStale === null ? undefined : ''}
                    disabled={stopBusy || !view.contract_compatible}
                    aria-disabled={stopBusy || !view.contract_compatible ? 'true' : 'false'}
                    onClick={onStop}
                    title={
                      stopStale !== null
                        ? stopStale.title
                        : replaying
                        ? 'Ask the LIVE runtime to stop its run and its mission. It does not act on the recording on screen.'
                        : 'Ask the runtime to stop the controller run and the mission. This is not an emergency stop.'
                    }
                  >
                    {stopBusy ? 'Stopping' : 'Stop'}
                  </Button>
                )}
                {/* THE FOCUSED DIAGNOSTICS DRAWER: History, Logs, Evidence and Connections, one scope. It opens on
                    Connections, which answers the header's own questions (which robot, and whether it answers). */}
                <Button variant="ghost" data-control="diagnostics" onClick={() => setDrawer('connections')}>
                  {DRAWER_NAME}
                </Button>
              </div>
            </div>
            {startOff === null ? null : (
              <span
                id="start-off-reason"
                data-control-reason="start"
                style={{ fontSize: 12, color: C.warn, maxWidth: 560, lineHeight: 1.35, textAlign: 'right', textWrap: 'pretty' }}
              >
                Start is off. {asSentence(startOff)}
              </span>
            )}
          </div>
        </header>

        {/* --- the phase rail, the target, the progress and the ONE mission, and which runtime this is --- */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 18,
            minHeight: 54,
            padding: '0 20px',
            borderBottom: '1px solid var(--color-border)',
            flex: 'none',
            flexWrap: 'wrap',
            opacity: robotStale ? 0.72 : 1,
          }}
        >
          <PhaseRail rows={rail.rows} />
          {/* DIMMED IS NOT ENOUGH. When the robot stops answering the rail keeps its rows and says they are retained. */}
          {robotStale ? <StaleTag ageMs={link.residentAgeMs} title="The rail is kept from the last answer; the robot is not answering now." /> : null}
          <div style={{ width: 1, height: 24, background: 'var(--color-border)', flex: 'none' }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 22, flex: 'none' }}>
            <div>
              {/* WHICH TARGET THE RAIL IS ABOUT, and the state the mission records for it, worded for how the mission
                  executes: a rehearsal's terminal target is what the rehearsal recorded, never a cube on a pad. */}
              <div style={KICKER}>Target</div>
              <div
                title={
                  rail.target === null
                    ? target.basis
                    : `${rail.target.label}: ${targetStateWords(rail.target.state, mission)}. ${rail.basis}. The state is the one this mission records for ${rail.target.target_id}.`
                }
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  marginTop: 2,
                  color: (rail.target === null ? target.waiting : rail.target.state === 'WAITING_FOR_CAPABILITY') ? '#ddd6fe' : INK.bright,
                }}
              >
                {rail.target === null ? target.label : `${rail.target.label}, ${targetStateShort(rail.target.state, mission)}`}
              </div>
            </div>
            <div>
              <div style={KICKER}>Progress</div>
              <div
                title={mission === null ? 'the placed predicate for the run on the screen' : `${missionPlaced(mission).words}. ${missionPlaced(mission).basis}`}
                style={{ fontSize: 13, fontWeight: 500, marginTop: 2, color: INK.soft }}
              >
                {progressWords}
              </div>
            </div>
            {/* THE ONE MISSION, stated once: its id and its state together. It replaces the mission timer, which the
                record cannot support as a duration, and the separate mission-state figure. */}
            <div>
              <div style={KICKER}>Mission</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 7, marginTop: 2 }}>
                {mission === null ? (
                  <span
                    title={snapshot.missionError ?? 'no mission has been read from this runtime'}
                    style={{ fontSize: 13, color: 'var(--color-muted-foreground)' }}
                  >
                    none on this runtime
                  </span>
                ) : (
                  <>
                    <span
                      title={`mission ${mission.mission_id}${mission.execution_label === null ? '' : `, execution ${mission.execution_label}`}${
                        mission.detail === null ? '' : `. ${mission.detail}`
                      }`}
                      style={{
                        fontSize: 13,
                        fontWeight: 500,
                        color: mission.state === 'FAILED' ? C.bad : mission.state === 'WAITING_FOR_CAPABILITY' ? '#ddd6fe' : INK.soft,
                      }}
                    >
                      {mission.state === 'WAITING_FOR_CAPABILITY' ? 'held' : missionStateWords(mission.state, mission.waiting_reason)}
                    </span>
                    <span style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 11, color: 'var(--color-muted-foreground)' }}>
                      {mission.mission_id.slice(-6)}
                    </span>
                    {missionRetained ? <StaleTag ageMs={missionAgeMs} title={`the last mission read failed: ${snapshot.missionError ?? ''}`} /> : null}
                  </>
                )}
              </div>
            </div>
          </div>
          <div style={{ flex: 1 }} />
          {/* The corner the prototype dock had: which runtime mode this is and how old its last snapshot is. */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 9,
              flex: 'none',
              padding: '5px 10px',
              borderRadius: 7,
              background: SURFACE.chip,
              border: '1px solid var(--color-border)',
            }}
          >
            <span
              style={{
                font: '600 11px var(--font-sans), sans-serif',
                letterSpacing: '.09em',
                textTransform: 'uppercase',
                color: mode === 'live' ? C.ok : mode === 'replay' ? C.wait : C.warn,
              }}
            >
              {mode === 'synthetic' ? 'simulation' : (mode ?? 'mode not recorded')}
            </span>
            <span style={{ font: '400 12px var(--font-sans), sans-serif', color: snapshotStale ? C.warn : 'var(--color-muted-foreground)', whiteSpace: 'nowrap' }}>
              {snapshotAgeMs === null ? 'no snapshot yet' : `snapshot ${ageWords(snapshotAgeMs)} ago`}
            </span>
            {snapshotStale ? <StaleTag /> : null}
          </div>
        </div>

        {/* Above everything: the alerts, the one command whose execution is not known, the robot's connection when it is
            not answering, and the motion hold. Nothing may hide these. */}
        <div className="console__body" style={{ flex: 'none', padding: '0 20px' }}>
          <StripAlerts snapshot={snapshot} mode={mode} sourceKind={snapshot.sourceKind} />
          <UncertainPanel snapshot={snapshot} store={store} />
          <RobotConnectionBanner link={link} />
          <MotionHold permission={permission} link={link} snapshot={snapshot} store={store} readOnlyReason={readOnly ? readOnlyReason : null} />
        </div>

        {/* --- the workspace --- */}
        <main
          style={{
            flex: 1,
            minHeight: 0,
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) minmax(360px, 400px)',
            gap: 16,
            padding: '16px 20px 20px',
          }}
        >
          <section
            style={{
              minWidth: 0,
              minHeight: 0,
              display: 'grid',
              // THE CAMERAS STAY THE LARGEST ELEMENT. They keep a floor, and when the action card, the telemetry line and
              // the controls row need more than the column has, the column scrolls instead of the pictures collapsing to
              // a strip. The capability request is not in this column: it is in the Cake panel's request slot.
              gridTemplateRows: 'minmax(240px, 1fr) auto auto auto auto',
              // ONE column that never grows past the section: a long unbroken line (the newest record) must be cut with
              // an ellipsis inside it, not widen the column and push the front camera under the Cake panel.
              gridTemplateColumns: 'minmax(0, 1fr)',
              gap: 10,
              overflowY: 'auto',
              overflowX: 'hidden',
            }}
          >
            <div style={{ minHeight: 0, display: 'grid', gridTemplateColumns: camCols, gap: 12 }}>
              <CameraCard panel={wrist} binding={wristStream.binding} enlarged={enlarged === 'wrist'} onEnlarge={() => setEnlarged('wrist')} />
              <CameraCard
                panel={front}
                binding={frontStream.binding}
                enlarged={enlarged === 'front'}
                onEnlarge={() => setEnlarged('front')}
                awaiting={
                  front.streamUrl !== null || view.data_mode !== 'live'
                    ? undefined
                    : 'This camera is held off because its qualification step has not passed, not because it was shown to cause harm. The controlled A/B/A did not separate one stream from two, and the measurement itself is now the open question. The measurements are in Connections.'
                }
              />
            </div>

            {/* --- what the robot is doing, said once --- */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 16,
                padding: '12px 18px',
                border: '1px solid var(--color-border)',
                borderLeft: `3px solid ${ruleColour}`,
                borderRadius: 12,
                background: SURFACE.raised,
                flex: 'none',
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={KICKER}>Current action</div>
                <div
                  style={{
                    fontSize: 19,
                    fontWeight: 600,
                    letterSpacing: '-.01em',
                    marginTop: 3,
                    color: robotStale ? '#fecaca' : INK.bright,
                    textWrap: 'pretty',
                  }}
                >
                  {card.title}
                </div>
                {/* ONE SENTENCE PER FACT, each on its own line. The finished run's outcome line is History's. */}
                {card.lines.map((line) => (
                  <div key={line} style={{ fontSize: 13, color: INK.quiet, marginTop: 4, lineHeight: 1.4, textWrap: 'pretty' }}>
                    {line}
                  </div>
                ))}
              </div>
              {result !== null && !running ? (
                <button type="button" onClick={() => setDrawer('history')} style={{ ...PLAIN_BUTTON, padding: '4px 11px' }}>
                  History
                </button>
              ) : null}
            </div>

            {/* --- live telemetry, one line --- */}
            <TelemetryStrip snapshot={snapshot} nowMs={nowMs} stale={robotStale} simulated={view.data_mode !== 'live' || snapshot.sourceKind === 'synthetic'} />

            {/* --- Observe and Return arm: the same Actions panel, the same guards and the same scope sentences --- */}
            <div className="controls-row">
              <Actions snapshot={snapshot} store={store} only={['observe', 'return_arm']} />
            </div>

            {/* --- the newest record, and the way into the whole of it --- */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 4px', flex: 'none', minHeight: 26 }}>
              <span style={{ ...KICKER, flex: 'none' }}>Latest</span>
              <span
                style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 12, color: 'var(--color-muted-foreground)', flex: 'none' }}
              >
                {latest?.t ?? '--:--:--'}
              </span>
              <span
                style={{ width: 6, height: 6, borderRadius: '50%', flex: 'none', background: latest === null ? INK.faint : TONE_DOT[latest.tone] }}
              />
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  fontSize: 13,
                  color: latest === null ? INK.quiet : TONE_INK[latest.tone],
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {/* A SIMULATION's record is the rehearsal's, and its outcome words (placed_new) are said as that. */}
                {latest === null
                  ? 'No record has reached this tab.'
                  : view.data_mode === 'synthetic' || snapshot.sourceKind === 'synthetic'
                    ? `Rehearsal record: ${latest.text}`
                    : latest.text}
              </span>
              <button type="button" onClick={() => setDrawer('history')} style={{ ...PLAIN_BUTTON, padding: '3px 10px' }}>
                History
              </button>
              <button type="button" onClick={() => setDrawer('logs')} style={{ ...PLAIN_BUTTON, padding: '3px 10px' }}>
                Logs
              </button>
              <button type="button" onClick={() => setDrawer('evidence')} style={{ ...PLAIN_BUTTON, padding: '3px 10px' }}>
                Evidence
              </button>
            </div>
          </section>

          {/* --- Cake, on the robot, as it actually is --- */}
          <CakeColumn snapshot={snapshot} store={store} nowMs={nowMs} readOnlyReason={readOnly ? readOnlyReason : null} running={running} onOpenDrawer={setDrawer} />
        </main>

        {/* THE RECEIPT FOR THE LAST START OR STOP, until it is dismissed or replaced. It says what the runtime said, or
            why nothing was sent, and never that motion ceased. */}
        {receipt === null ? null : (
          <div
            role="status"
            data-receipt={receipt.what.toLowerCase()}
            style={{
              position: 'fixed',
              left: 20,
              bottom: 20,
              zIndex: 45,
              maxWidth: 560,
              padding: '12px 14px',
              borderRadius: 10,
              // OPAQUE UNDER THE TINT: the receipt floats over the workspace, and a see-through one let the controls under
              // it show through its words.
              backgroundColor: SURFACE.raised,
              backgroundImage: `linear-gradient(${wash(receipt.tone === 'taken' ? C.ok : receipt.tone === 'refused' ? C.bad : C.warn, 0.12)}, ${wash(
                receipt.tone === 'taken' ? C.ok : receipt.tone === 'refused' ? C.bad : C.warn,
                0.12,
              )})`,
              border: `1px solid ${wash(receipt.tone === 'taken' ? C.ok : receipt.tone === 'refused' ? C.bad : C.warn, 0.4)}`,
              boxShadow: '0 6px 24px rgba(0,0,0,.45)',
              color: receipt.tone === 'taken' ? INK.bright : receipt.tone === 'refused' ? '#fecaca' : '#fde68a',
              fontSize: 13,
              lineHeight: 1.45,
            }}
          >
            <strong>{receipt.what}:</strong> {receipt.text}
            <button type="button" onClick={() => setReceipt(null)} style={{ ...PLAIN_BUTTON, marginLeft: 12 }}>
              Dismiss
            </button>
          </div>
        )}

        {attesting && !running ? (
          <Dialog
            title="Before the mission starts"
            onClose={() => setAttesting(false)}
            actions={
              <>
                <button type="button" className="button" onClick={() => setAttesting(false)}>
                  cancel
                </button>
                <button
                  type="button"
                  className="button button--primary"
                  // OFF WHENEVER THE HEADER'S START IS OFF (refix round 9, the class of verify-r8 M1): the dialog never offers
                  // a command the runtime would refuse.
                  disabled={startDisabled || !checked.every((item) => item)}
                  onClick={() => {
                    setAttesting(false);
                    runStart();
                  }}
                >
                  start the mission
                </button>
              </>
            }
          >
            {startOff === null ? null : (
              <p data-control-reason="attest-start" style={{ color: C.warn }}>
                Start is off. {asSentence(startOff)}
              </p>
            )}
            <p>You are stating that you checked these. The record will say attested, not verified.</p>
            {ATTESTATIONS.map((line, index) => (
              <label className="check" key={line}>
                <input
                  type="checkbox"
                  checked={checked[index] ?? false}
                  onChange={(event) => setChecked((current) => current.map((item, at) => (at === index ? event.target.checked : item)))}
                />
                <span>{line}</span>
              </label>
            ))}
            <p className="muted">{ATTACH_IS_DECIDED_ON_THE_ROBOT}</p>
          </Dialog>
        ) : null}

        {drawer === null ? null : (
          <Drawer id={drawer} snapshot={snapshot} store={store} mode={mode} top={headerHeight} onSelect={setDrawer} onClose={() => setDrawer(null)} />
        )}
      </div>
    </StopBarHeight>
  );
}
