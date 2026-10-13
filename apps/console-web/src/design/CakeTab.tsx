// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// DIAGNOSTICS, CAKE (docs/autonomous-demo/console-redesign-2026-09-27.md, section 7.1).
//
// What the redesign moved off the demo screen, about the robot's resident: the modules it lists with the holder slot
// and the controller's own card (which says it is not a Cake module), the capability slot check with its Apply buttons
// and its records, each with its resident session and its date, the signature table's rows (the Run button stays on the
// main card only), whether a Cake module took part in the task as the runtime served it, and each mission activation's
// details (Plan and payload digests, transaction uuid and handle, the slot reads) under the same verdict words as the
// main screen's cards. Nothing here is a mission record but the activations.

import type { ReactNode } from 'react';
import { checkUnavailableWords } from '../model/capabilityCheck';
import type { CakeParticipation } from '../model/cakeTask';
import { activationCard } from '../model/activationCard';
import { utcDayOf } from '../model/utcTime';
import type { FoldedRun } from '../model/stageTimeline';
import type { ConsoleStore, StoreSnapshot } from '../store/store';
import { useNowMs } from '../store/useConsole';
import { runtimeAnswerBlock } from '../panels/commandGuards';
import { ActivationEvidence } from './ActivationEvidence';
import { controllerCard, hostOutsideCake, moduleCards } from './cakeModules';
import type { ModuleCard } from './cakeModules';
import { CakePanel } from './CakePanel';
import { SignatureCheckBlock } from './SignatureCheck';
import { INK } from './theme';
import { TONE_COLOUR } from './TopBar';

/** The bundle the runs were executed from, as their events' producer names it. Null when none was recorded. */
export function bundleDigestOf(runs: readonly FoldedRun[]): string | null {
  for (const run of runs) {
    const found = run.view.recent_events.find((event) => event.producer.artifact_digest !== null);
    if (found !== undefined) return found.producer.artifact_digest;
  }
  return null;
}

/** The module cards: every slot the resident lists, then the controller's own card. None while no robot was read. */
export function demoModuleCards(snapshot: StoreSnapshot, runs: readonly FoldedRun[]): ModuleCard[] {
  const robot = snapshot.robot;
  const capabilitySlot = snapshot.capabilityCheck?.current.slot?.slot_uuid ?? robot?.slots.items.find((item) => item.name === robot.capability.name)?.uuid ?? null;
  const slots = robot === null ? [] : moduleCards(robot.slots.items, robot.health.items, capabilitySlot);
  return [...slots, controllerCard(bundleDigestOf(runs))];
}

/**
 * WHY THE RESIDENT'S MODULES ARE NOT DRAWN while no reading of the robot is held (verify-r2 must_fix 4): the failed
 * read's code, or that it has not been read yet. Null once a reading is held. An empty list is said only of a reading.
 */
export function modulesNotRead(snapshot: Pick<StoreSnapshot, 'robot' | 'robotError'>): string | null {
  if (snapshot.robot !== null) return null;
  return snapshot.robotError === null ? "The resident's modules have not been read yet." : `The resident's modules were not read: ${snapshot.robotError}.`;
}

/** Participation as the runtime served it in the cake ladder; null when the rung decided nothing. */
export function servedParticipation(snapshot: StoreSnapshot, moduleName: string | null): CakeParticipation | null {
  const rung = snapshot.view.cake.ladder.mission_participation;
  if (rung.value === null) return null;
  return { value: rung.value, evidence: rung.evidence.replace(/\.$/, ''), basis: null, module: moduleName, generation: null, pid: null, applied: null, target_id: null };
}

export function CakeTab(props: { snapshot: StoreSnapshot; store: ConsoleStore }): ReactNode {
  const nowMs = useNowMs();
  const { snapshot, store } = props;
  const view = snapshot.view;
  const mission = snapshot.mission;
  const readOnly = snapshot.replay !== null || !view.contract_compatible || !view.commands_enabled;
  // NOT OFFERED WHILE THE RUNTIME IS NOT ANSWERING or its snapshot is not current (verdict 2026-09-27 must_fix 3).
  const readOnlyReason = readOnly ? 'this tab may not command the robot now' : runtimeAnswerBlock(snapshot, nowMs);
  const capabilityName = snapshot.robot?.configured === true ? snapshot.robot.capability.name : null;
  // A MISSION THAT HAS NOT ENDED OWNS THE REQUEST SLOT: the check is offered only when there is none, or it has ended.
  const slotHeld = mission !== null && (mission.state === 'RUNNING' || mission.state === 'WAITING_FOR_CAPABILITY');
  const check = slotHeld ? null : snapshot.capabilityCheck;
  const checkUnavailable = slotHeld
    ? 'a mission holds the request slot until it ends'
    : snapshot.capabilityCheckError !== null
      ? checkUnavailableWords(snapshot.capabilityCheckError, snapshot.capabilityCheckErrorDetail)
      : view.data_mode === 'live'
        ? 'the runtime has not answered a read of the check yet'
        : 'this runtime offers no capability slot check';
  const runs: FoldedRun[] = [{ run_id: view.active_run?.run_id ?? '', view }, ...Object.entries(snapshot.runFolds).map(([runId, held]) => ({ run_id: runId, view: held }))];
  const onApply = (key: string): void => {
    const state = snapshot.capabilityCheck;
    if (state === null) return;
    const digest = state.candidates.find((item) => item.key === key)?.payload_digest ?? null;
    const session = state.current.session_uuid;
    if (digest === null || session === null) return;
    void store.applyCapabilityCheck(key, digest, session);
  };
  return (
    <div data-cake-tab="" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <CakePanel
        check={check}
        checkUnavailable={checkUnavailable}
        readAtMs={snapshot.capabilityCheckReadAtMs}
        nowMs={nowMs}
        day={utcDayOf(mission?.started_wall_utc ?? null) ?? new Date(nowMs).toISOString().slice(0, 10)}
        applying={snapshot.checking}
        readOnlyReason={readOnlyReason}
        checkNotRead={snapshot.capabilityCheck === null ? snapshot.capabilityCheckError : null}
        onApply={onApply}
        modules={demoModuleCards(snapshot, runs)}
        modulesNotRead={modulesNotRead(snapshot)}
        modulesReadFailed={snapshot.robot !== null ? snapshot.robotError : null}
        participation={servedParticipation(snapshot, capabilityName)}
        hostFinding={hostOutsideCake(snapshot.robot?.host ?? null)}
        signature={
          <SignatureCheckBlock
            state={snapshot.signatureCheck}
            readError={snapshot.signatureCheckError}
            readErrorDetail={snapshot.signatureCheckErrorDetail}
            live={view.data_mode === 'live'}
            running={snapshot.signatureChecking}
            lastRun={snapshot.lastSignatureCheck}
            readOnlyReason={readOnlyReason}
            onRun={() => undefined}
            rowsOnly
            plainLabels
            runtimeSession={view.runtime_session}
          />
        }
      />
      <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 13, color: INK.quiet }}>This mission&apos;s activations, in the order the runtime recorded them</div>
        {mission === null && snapshot.missionError !== null && snapshot.missionError !== 'no_mission_recorded' ? (
          // A MISSION READ THAT FAILED is not a mission with no activation (verify-r1 must_fix 3).
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>The mission has not been read: {snapshot.missionError}.</p>
        ) : mission === null || mission.activations.length === 0 ? (
          <p style={{ margin: 0, fontSize: 13, color: INK.quiet }}>This mission has recorded no activation.</p>
        ) : (
          mission.activations.map((activation) => {
            // THE MISSION'S DAY (spec 5.4; browser r2 N6): a time of that day is printed as the card prints it.
            const card = activationCard(activation, snapshot.missionCandidates, utcDayOf(mission.started_wall_utc));
            return (
              <div key={activation.operation_uuid} data-activation-detail={activation.operation_uuid} style={{ borderTop: '1px solid var(--color-border)', paddingTop: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: TONE_COLOUR[card.verdict.tone] }}>{card.headline}</div>
                <div style={{ fontSize: 12, color: INK.soft }} data-t={card.stamp?.iso} title={card.stamp?.title}>
                  {card.line}
                </div>
                {card.answer === null ? null : <div style={{ fontSize: 12, color: INK.quiet, lineHeight: 1.45 }}>{card.answer}</div>}
                <ActivationEvidence activation={activation} full />
              </div>
            );
          })
        )}
      </section>
    </div>
  );
}
