// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DECISION FRAME MUST NOT FOLLOW THE PREVIEW.
//
// The live preview moves; the decision frame does not. A result shown beside a later picture is a result nobody can
// check, and the failure mode this pins is the tempting one: telemetry is arriving, it carries a picture, and the
// panel that shows "what the controller decided on" quietly starts showing the newest one instead.
//
// These tests drive the STORE, not the reducer, because the store is where the two streams meet: the reducer folds
// contract events and the telemetry subscription runs beside it.

import { describe, expect, it } from 'vitest';
import type { Command, CommandResult, Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import type { ContractEvent, EventEnvelope } from '../contracts/envelope';
import type {
  ActivationOutcome,
  CakeStatusResult,
  CapabilityCheckOutcome,
  CapabilityCheckResult,
  SignatureCheckOutcome,
  SignatureCheckResult,
  ConsoleSource,
  EventsMessage,
  HelloResult,
  HistoryEventsResult,
  HistoryResult,
  MissionCandidatesResult,
  MissionResult,
  MissionStartOutcome,
  MissionStopOutcome,
  ObservationRead,
  PreviewSample,
  ResolutionOutcome,
  RunOutputResult,
  StopAck,
  TapSample,
  TelemetryMessage,
  Unsubscribe,
  RobotConfirmOutcome,
  RobotStatusResult,
} from '../api/types';
import type { ObservationRecordDto, PerceptionDto } from '../model/observation';
import { ConsoleStore } from './store';

let seq = 0;

function envelope(extra: Partial<EventEnvelope> = {}): EventEnvelope {
  seq += 1;
  return {
    schema_version: '1',
    seq,
    run_id: 'run-d',
    action_id: null,
    observation_id: null,
    producer: { name: 'test', version: '0', artifact_digest: null, contract_version: '1' },
    source_session: 'session-d',
    t_mono_ns: seq * 1_000_000,
    clock_domain: 'laptop_monotonic',
    t_wall_utc: null,
    reason_code: null,
    provenance: null,
    ...extra,
  };
}

function capture(observationId: string): ContractEvent {
  return {
    ...envelope({ observation_id: observationId }),
    kind: 'observation.captured',
    payload: { source_role: 'wrist', media: 'frame', width_px: 640, height_px: 480, content_digest: null, source_stamp: null, retained: true },
  } as ContractEvent;
}

function predicate(name: string, observationIds: string[]): ContractEvent {
  return {
    ...envelope(),
    kind: 'predicate.evaluated',
    payload: {
      predicate: name,
      value: true,
      threshold: 3,
      measured: 30,
      unit: 'mm',
      evidence: [{ type: 'frame', observation_ids: observationIds, scalar_name: null }],
      consumed_source_stamp: null,
    },
  } as ContractEvent;
}

function previewSample(seqNumber: number): PreviewSample {
  return {
    role: 'wrist',
    seq: seqNumber,
    capture_clock: { session: 'camera-session-a', seq: seqNumber, sample_mono_ns: seqNumber * 1000, clock_domain: 'pi_monotonic' },
    receive_age_ms: 40,
    measured_fps: 9.5,
    available: true,
    reason: null,
    state: 'connected',
  };
}

/** A source this test drives by hand: it pushes events and telemetry when the test says so and at no other time. */
class DrivenSource implements ConsoleSource {
  readonly kind = 'live' as const;
  readonly label = 'a source under test';
  private events: ((message: EventsMessage) => void) | null = null;
  private telemetry: ((message: TelemetryMessage) => void) | null = null;
  readonly fetchedObservations: string[] = [];

  async hello(): Promise<HelloResult> {
    return { ok: true, contract_version: '1', runtime_session: 'rt-a', reason_code: null, read_only: false, commands_enabled: true, credential_owner: 'runtime_session' };
  }

  async snapshot(): Promise<Snapshot> {
    return runningSnapshot as unknown as Snapshot;
  }

  subscribeEvents(_since: number, onMessage: (message: EventsMessage) => void): Unsubscribe {
    this.events = onMessage;
    return () => {
      this.events = null;
    };
  }

  subscribeTelemetry(onMessage: (message: TelemetryMessage) => void): Unsubscribe {
    this.telemetry = onMessage;
    return () => {
      this.telemetry = null;
    };
  }

  pushEvent(event: ContractEvent): void {
    this.events?.({ event });
  }

  pushPreview(sample: PreviewSample): void {
    this.telemetry?.({ preview: sample });
  }

  pushTap(sample: TapSample): void {
    this.telemetry?.({ tap: sample });
  }

  async submit(commandId: string, _command: Command): Promise<{ kind: 'result'; result: CommandResult }> {
    return { kind: 'result', result: { command_id: commandId, status: 'accepted', reason_code: null, run_id: 'run-d' } };
  }

  async resolve(): Promise<CommandResult | null> {
    return null;
  }

  async resolveUncertain(): Promise<ResolutionOutcome> {
    return { kind: 'unknown', detail: 'this source does not reconcile' };
  }

  async stop(): Promise<StopAck> {
    return { ok: false, command_id: 'stop-not-under-test', result: null, requested_mono_ns: null, run_id: null, reason: 'not under test', outcome: 'refused', mission: null };
  }

  async observationRecord(observationId: string): Promise<ObservationRead<ObservationRecordDto>> {
    this.fetchedObservations.push(observationId);
    return { data: null, reason: 'not under test' };
  }

  async observationPerception(): Promise<ObservationRead<PerceptionDto>> {
    return { data: null, reason: 'not under test' };
  }

  frameUrl(observationId: string): string {
    return `/api/observations/${observationId}/frame`;
  }

  previewUrl(): string {
    return '/api/preview.mjpg';
  }

  async history(): Promise<HistoryResult> {
    return { ok: true, entries: [] };
  }

  async historyEvents(): Promise<HistoryEventsResult> {
    return { ok: false, reason_code: 'not under test' };
  }

  async runOutput(): Promise<RunOutputResult> {
    return { ok: false, reason_code: 'not under test' };
  }

  async cakeStatus(): Promise<CakeStatusResult> {
    return { ok: false, reason_code: 'not under test' };
  }

  async mission(): Promise<MissionResult> {
    return { ok: false, reason_code: 'not under test' };
  }

  async missionCandidates(): Promise<MissionCandidatesResult> {
    return { ok: false, reason_code: 'not under test' };
  }

  async activateCandidate(): Promise<ActivationOutcome> {
    return { kind: 'refused', reason_code: 'not under test' };
  }

  async missionStart(): Promise<MissionStartOutcome> {
    return { kind: 'refused', reason_code: 'not under test', detail: null };
  }

  async missionStop(): Promise<MissionStopOutcome> {
    return { kind: 'refused', reason_code: 'not under test', detail: null };
  }

  async robotStatus(): Promise<RobotStatusResult> {
    return { ok: false, reason_code: 'not under test' };
  }

  async confirmRobotSession(): Promise<RobotConfirmOutcome> {
    return { kind: 'refused', reason_code: 'not under test' };
  }

  async capabilityCheck(): Promise<CapabilityCheckResult> {
    return { ok: false, reason_code: 'not under test', detail: null };
  }

  async applyCapabilityCheck(): Promise<CapabilityCheckOutcome> {
    return { kind: 'refused', reason_code: 'not under test', detail: null };
  }

  async signatureCheck(): Promise<SignatureCheckResult> {
    return { ok: false, reason_code: 'not under test', detail: null };
  }

  async runSignatureCheck(): Promise<SignatureCheckOutcome> {
    return { kind: 'refused', reason_code: 'not under test', detail: null };
  }
}

async function settle(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('the decision frame does not follow the preview', () => {
  it('stays on the frame a predicate consumed while preview frames keep arriving', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();

    source.pushEvent(capture('obs-decided-0001'));
    source.pushEvent(predicate('pad_frame_valid', ['obs-decided-0001']));
    await settle();
    const pinnedAt = store.getSnapshot().view.decision_frame?.pinned_at_seq;
    expect(store.getSnapshot().view.decision_frame?.primary_observation_id).toBe('obs-decided-0001');
    expect(store.getSnapshot().frame.observation_id).toBe('obs-decided-0001');

    for (let index = 1; index <= 20; index += 1) source.pushPreview(previewSample(index));
    await settle();

    // The preview moved twenty times. The pinned frame did not move at all, and nothing new was fetched for it.
    expect(store.getSnapshot().telemetry.preview?.seq).toBe(20);
    expect(store.getSnapshot().view.decision_frame?.primary_observation_id).toBe('obs-decided-0001');
    expect(store.getSnapshot().view.decision_frame?.pinned_at_seq).toBe(pinnedAt);
    expect(store.getSnapshot().frame.observation_id).toBe('obs-decided-0001');
    expect(source.fetchedObservations).toEqual(['obs-decided-0001']);
    store.dispose();
  });

  it('does not pin anything at all from the preview when no predicate has consumed evidence', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();

    for (let index = 1; index <= 5; index += 1) source.pushPreview(previewSample(index));
    await settle();

    expect(store.getSnapshot().telemetry.preview).not.toBeNull();
    // A preview frame is not evidence and is never a decision. The panel stays empty rather than showing a picture.
    expect(store.getSnapshot().view.decision_frame).toBeNull();
    expect(store.getSnapshot().frame.observation_id).toBeNull();
    expect(source.fetchedObservations).toEqual([]);
    store.dispose();
  });

  it('moves only when a predicate consumes different evidence, and a later capture alone does not move it', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();

    source.pushEvent(capture('obs-decided-0001'));
    source.pushEvent(predicate('pad_frame_valid', ['obs-decided-0001']));
    await settle();

    // A newer capture arrives, and preview frames keep arriving with it. Neither is a decision.
    source.pushEvent(capture('obs-newer-0002'));
    for (let index = 1; index <= 3; index += 1) source.pushPreview(previewSample(index));
    await settle();
    expect(store.getSnapshot().view.decision_frame?.primary_observation_id).toBe('obs-decided-0001');

    // Now a predicate consumes the newer one. THAT moves it.
    source.pushEvent(predicate('cube_in_the_window', ['obs-newer-0002']));
    await settle();
    expect(store.getSnapshot().view.decision_frame?.primary_observation_id).toBe('obs-newer-0002');
    expect(source.fetchedObservations).toEqual(['obs-decided-0001', 'obs-newer-0002']);
    store.dispose();
  });

  it('keeps the preview on the telemetry slot, which is never folded into the durable view', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();
    source.pushPreview(previewSample(7));
    await settle();

    const snapshot = store.getSnapshot();
    expect(snapshot.telemetry.preview?.seq).toBe(7);
    // Nothing about the preview reaches the folded record: no observation, no event, no cursor movement.
    expect(Object.keys(snapshot.view.observations)).toEqual([]);
    expect(snapshot.view.recent_events).toEqual([]);
    expect(snapshot.view.last_event_seq).toBeNull();
    store.dispose();
  });
});

function tapSample(seqNumber: number): TapSample {
  return {
    source_stamp: { session: 'host-session-a', seq: seqNumber, sample_mono_ns: seqNumber * 1000, clock_domain: 'pi_monotonic' },
    joints: null,
    base_velocity: null,
    receive_age_ms: 12,
    subscriber_gap_count: null,
    publisher_drop_count: null,
    state: 'connected',
  };
}

async function wait(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * How many times the telemetry slot reached the screen, which is the thing React pays for. It is counted off the
 * published snapshot and not off the source, because the point of the window is that the two differ.
 */
function countTelemetryPublications(store: ConsoleStore): { count: () => number; stop: Unsubscribe } {
  let publications = 0;
  let last = store.getSnapshot().telemetry;
  const stop = store.subscribe(() => {
    const telemetry = store.getSnapshot().telemetry;
    if (telemetry !== last) {
      publications += 1;
      last = telemetry;
    }
  });
  return { count: () => publications, stop };
}

describe('replaceable telemetry reaches the screen at the window rate, not at the arrival rate', () => {
  it('publishes a burst of samples once, with the last of them on the screen', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();
    const publications = countTelemetryPublications(store);

    for (let index = 1; index <= 20; index += 1) source.pushPreview(previewSample(index));
    // Receipt does not wait for React: twenty samples were taken in, and none of them woke a render on its own.
    expect(publications.count()).toBe(0);

    await settle();
    expect(publications.count()).toBe(1);
    expect(store.getSnapshot().telemetry.preview?.seq).toBe(20);
    publications.stop();
    store.dispose();
  });

  it('merges the two slots that arrive together into one publication', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();
    const publications = countTelemetryPublications(store);

    source.pushTap(tapSample(7));
    source.pushPreview(previewSample(8));
    await settle();

    expect(publications.count()).toBe(1);
    expect(store.getSnapshot().telemetry.tap?.source_stamp?.seq).toBe(7);
    expect(store.getSnapshot().telemetry.preview?.seq).toBe(8);
    publications.stop();
    store.dispose();
  });

  it('publishes a handful of times while twenty samples arrive, and never once per sample', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();
    const publications = countTelemetryPublications(store);

    for (let index = 1; index <= 20; index += 1) {
      source.pushPreview(previewSample(index));
      await wait(25);
    }
    await wait(250);

    expect(publications.count()).toBeGreaterThanOrEqual(2);
    expect(publications.count()).toBeLessThanOrEqual(8);
    expect(store.getSnapshot().telemetry.preview?.seq).toBe(20);
    publications.stop();
    store.dispose();
  });

  it('stamps a sample when it arrived and not when it was published', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();

    source.pushPreview(previewSample(1));
    await settle();
    const arrivedAt = Date.now();
    source.pushPreview(previewSample(2));
    await wait(250);

    expect(store.getSnapshot().telemetry.preview?.seq).toBe(2);
    // The arrival stamp is what every age in the browser's clock is measured from. A sample that waited for the
    // window would otherwise read as younger than it is.
    expect((store.getSnapshot().telemetry.preview_received_at_ms ?? 0) - arrivedAt).toBeLessThan(60);
    store.dispose();
  });

  it('publishes the sample that was still waiting when the store was disposed', async () => {
    const source = new DrivenSource();
    const store = new ConsoleStore(source);
    await store.start();

    source.pushPreview(previewSample(1));
    await settle();
    source.pushPreview(previewSample(9));
    store.dispose();

    expect(store.getSnapshot().telemetry.preview?.seq).toBe(9);
  });
});
