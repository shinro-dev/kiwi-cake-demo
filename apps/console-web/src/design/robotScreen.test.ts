// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE ROBOT ON THE RENDERED SCREEN.
//
// The operator's five questions, answered on the whole screen rendered to static markup: which LeKiwi, whether its
// resident and supervised host are there, what it is doing and what can be done, which capability is active and what
// changed, and whether video and telemetry are current. Each case pins the ruling that keeps one answer honest:
//
//   an unavailable robot shows the connection and marks what is retained STALE with its age;
//   a resident on this laptop, and a generated frame, are never shown as the robot's;
//   a held motion permission says why and offers the guarded confirmation with what it will post;
//   a component recovery is stated apart from a resident restart;
//   a rehearsal's result is never worded as a physical placement, and a finished run's line is History's;
//   a rehearsal's generated frames appear in Evidence only, never in a camera panel.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import type { CakeStatus, ConsoleSource } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import type { MissionRecord } from '../model/missionRecord';
import { applySnapshot, initialState } from '../model/reducer';
import { FIXTURE_HOST_PID, FIXTURE_SESSION, FIXTURE_SLOT_INSTANCE, robotBody, robotStatus } from '../model/robotStatusFixture';
import type { ObservationMeta, ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { cameraPanel } from './cameras';
import { Screen } from './Screen';

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

/** A LIVE runtime over HTTP, which is the robot deployment this screen is for. */
function liveView(): ViewState {
  return { ...applySnapshot(initialState(), runningSnapshot as unknown as Snapshot), data_mode: 'live' };
}

function words(patch: Partial<StoreSnapshot>, view: ViewState = liveView()): string {
  const store = new ConsoleStore(new SyntheticSource());
  const state = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...state, sourceKind: 'live', ...patch, view };
  return text(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen))));
}

const current = { robotReadAtMs: Date.now(), robotError: null };

describe('which LeKiwi, and whether its resident and host are there', () => {
  it('names the robot and states its resident, supervised host and capability', () => {
    const shown = words({
      ...current,
      robot: robotStatus({ identity: { ...(robotBody().identity as object), display_label: 'LeKiwi bench 1', labelled: true } }),
    });
    expect(shown).toContain('LeKiwi bench 1');
    expect(shown).toContain('Robot resident answering');
    expect(shown).toContain('Supervised host supervised, running');
    expect(shown).toContain(`pid ${String(FIXTURE_HOST_PID)} on the robot`);
    expect(shown).toContain('mobile_approach not active');
    expect(shown).toContain('EVT_SUPERVISOR_CHILD_STARTED');
  });

  it('says the name was not reported, and never substitutes the desktop runtime session for it', () => {
    const shown = words({ ...current, robot: robotStatus() });
    expect(shown).toContain('Robot name not reported');
    expect(shown).not.toContain(String(liveView().runtime_session));
  });
});

describe('an unavailable robot', () => {
  const lost = robotStatus({
    reachable: false,
    stale: true,
    reason_code: 'robot_unreachable',
    resident: { ...(robotBody().resident as object), reachable: false, stale: true, observed: { wall_utc: '2026-09-23T10:00:00Z', age_ms: 95_000 } },
  });

  it('shows the connection state and marks every retained reading STALE with its age', () => {
    const shown = words({ ...current, robot: lost });
    expect(shown).toContain('Robot not answering');
    expect(shown).toContain('robot_unreachable');
    expect(shown).toContain('STALE');
    expect(shown).toContain('1 min 35 s old');
    expect(shown).toContain('Cake resident last seen');
    // Supervision is never taken from a retained answer, and the live telemetry is unavailable, not the last numbers.
    expect(shown).toContain('Supervised host not current');
    expect(shown).toContain('arm and base unavailable: the robot is not answering');
    // The action card states the connection, never the last action.
    expect(shown).toContain('Current action Robot not answering');
  });

  it('marks the kept answer STALE when the runtime itself stops answering about the robot', () => {
    const shown = words({ robot: robotStatus(), robotError: 'the runtime did not answer', robotReadAtMs: Date.now() - 40_000 });
    expect(shown).toContain('Robot not answering');
    expect(shown).toContain('STALE');
  });
});

describe('the laptop resident is never the robot', () => {
  const laptopResident = {
    cake_status_version: '1', configured: true, reachable: true, reason_code: null, stale: false, session_changed: null,
    clock_domain: 'laptop_monotonic',
    observation: {
      wall_utc: '2026-09-23T10:27:24.757Z', mono_ns: 1, age_ms: 40,
      resident: { session_uuid: 'aa'.repeat(16), build_identity: null, config_identity: null, target_profile_digest: null, plan_digest: null },
      counts: { epoch: 0, slots: 3, bindings: 0, resources: 0, replay: 0, snapshot_present: 1 }, transaction_state: 'none', flight: null,
    },
    activity: { value: null, evidence: '' }, mission_participation: { value: false, evidence: '' },
  } as unknown as CakeStatus;

  it('draws no resident on the main screen when the runtime reads only one on this computer', () => {
    const shown = words({ ...current, cakeStatus: laptopResident, robot: robotStatus({ configured: false, deployment: 'this_laptop' }) });
    expect(shown).toContain('No robot configured');
    expect(shown).not.toContain('Resident answering');
    expect(shown).not.toContain('on this laptop');
    expect(shown).not.toContain('module slot');
    expect(shown).not.toContain('aa'.repeat(16));
  });
});

describe('motion permission', () => {
  it('says why motion is held and offers the confirmation with the session and host instance it will post', () => {
    const shown = words({ ...current, robot: robotStatus() });
    expect(shown).toContain('Robot motion Held');
    expect(shown).toContain('Never confirmed');
    expect(shown).toContain('robot_session_not_confirmed');
    expect(shown).toContain('Confirm this robot');
    expect(shown).toContain(`session ${FIXTURE_SESSION} · host slot instance ${FIXTURE_SLOT_INSTANCE}, spawn 2, pid ${String(FIXTURE_HOST_PID)}`);
    // Observe is not a motion command, and the banner says so.
    expect(shown).toContain('Observe does not move the robot');
  });

  it('names a resident restart as the cause, apart from a component recovery', () => {
    const restarted = robotStatus({
      resident: { ...(robotBody().resident as object), session_changed: true },
      motion_permission: { ...(robotBody().motion_permission as object), reason_code: 'robot_session_changed', confirmed_session_uuid: 'ff'.repeat(16) },
    });
    const shown = words({ ...current, robot: restarted });
    expect(shown).toContain('The resident restarted');
    expect(shown).toContain('Resident restart: the robot');
    expect(shown).toContain('Component recovery');
  });

  it('offers no Confirm for pid 0, a held or DEGRADED host, or an unidentified resident', () => {
    const motion = robotBody().motion_permission as Record<string, unknown>;
    const notRunning = robotStatus({ motion_permission: { ...motion, confirmable: false, current_host_instance: null, current_host_reason_code: 'robot_host_not_running' } });
    const zero = robotStatus({ motion_permission: { ...motion, current_host_instance: { slot_instance: FIXTURE_SLOT_INSTANCE, spawn_sequence: 2, pid: 0 } } });
    const impostor = robotStatus({
      reachable: false,
      identity: { ...(robotBody().identity as object), is_robot: false, reason_code: 'robot_resident_not_the_robot' },
      motion_permission: { ...motion, reason_code: 'robot_resident_not_the_robot', confirmable: false, current_session_uuid: null, current_host_instance: null },
    });
    for (const robot of [notRunning, zero, impostor]) {
      const shown = words({ ...current, robot });
      expect(shown).toContain('Robot motion Held');
      expect(shown).not.toContain('Confirm this robot');
      expect(shown).toContain('Nothing to confirm');
    }
    expect(words({ ...current, robot: notRunning })).toContain('no host is running under the supervisor slot');
  });

  it('says a revoked confirmation was revoked, and which component changed, instead of "never confirmed"', () => {
    const revoked = robotStatus({
      motion_permission: { ...(robotBody().motion_permission as object), reason_code: 'robot_host_changed', changed_component: 'spawn_sequence' },
    });
    const shown = words({ ...current, robot: revoked });
    expect(shown).toContain('What changed: the supervisor started the host again');
    expect(shown).toContain('was revoked by that change');
    expect(shown).not.toContain('No session has been confirmed on this runtime');
  });

  it('draws no hold when the runtime reports motion permitted on a current reading', () => {
    const permitted = robotStatus({ motion_permission: { ...(robotBody().motion_permission as object), permitted: true, reason_code: null } });
    const shown = words({ ...current, robot: permitted });
    expect(shown).not.toContain('Robot motion Held');
    expect(shown).not.toContain('Confirm this robot');
  });
});

describe('a rehearsal is never a placement', () => {
  const rehearsal: MissionRecord = {
    mission_record_version: '1',
    mission_id: 'mission-0002',
    state: 'COMPLETE',
    data_mode: 'synthetic',
    performs_motion: false,
    execution_label: 'recording-rehearsal',
    started_wall_utc: '2026-09-23T10:00:00.000Z',
    selected_target: null,
    waiting_reason: null,
    detail: null,
    targets: [
      { target_id: 'cube_near', label: 'the reachable cube', reachable: true, requires_capability: false, state: 'PLACED', runs: ['run-1'] },
      { target_id: 'cube_far', label: 'the far cube', reachable: false, requires_capability: true, state: 'PLACED', runs: ['run-2'] },
    ],
    activations: [],
  };

  // DELIBERATELY CHANGED. This case pinned the finished run's one-line summary ON THE MAIN SCREEN, in the action card.
  // The design review's ruling (the "Remove from the current UI" table: repeated completion statements and outcomes go
  // to History) and the lead's item 6 moved that line to History, so it is now asserted there, whole and unchanged,
  // and asserted ABSENT from the main screen. The rule it pins is the same: a rehearsal's line carries its REHEARSAL
  // mark and its figures and is never worded as a placement.
  it("shows a finished rehearsal run's line in History, with its figures and its REHEARSAL mark, never as placed, and not on the main screen", async () => {
    const measured = (await import('@kiwi/demo-contracts/fixtures/event/predicate.evaluated.evaluated-with-measured-evidence.json')).default;
    const finished = (await import('@kiwi/demo-contracts/fixtures/event/run.finished.json')).default;
    const { applyEvent } = await import('../model/reducer');
    const { Drawer } = await import('./Drawers');
    type Event = Parameters<typeof applyEvent>[1];
    const view = applyEvent(applyEvent(liveView(), measured as unknown as Event), finished as unknown as Event);
    const history = (dataMode: 'synthetic' | 'live'): string => {
      const store = new ConsoleStore(new SyntheticSource());
      const snapshot = { ...store.getSnapshot(), sourceKind: 'live' as const, view: { ...view, data_mode: dataMode } };
      return text(
        renderToStaticMarkup(
          createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'history', snapshot, store, mode: dataMode, onSelect: () => {}, onClose: () => {} })),
        ),
      );
    };
    const line =
      // The spec 5.1 word, as the mission card gives a rehearsal's run, with the sentence that no physical placement was
      // made (browser r2 N2, verifier R-4); the record's token only in a title.
      "Rehearsal. The rehearsal's run ended: Placed; nothing was physically moved by it. What the rehearsal's own records measured, not a physical measurement: Cube-centre edge margin: 30 mm; required: 3 mm.";
    expect(history('synthetic')).toContain(line);
    expect(history('synthetic')).not.toContain('Placed and returned');
    expect(history('live')).toContain('Live. Placed and returned.');
    // The main screen says where it is and does not restate it.
    const shown = words({}, { ...view, data_mode: 'synthetic' as const });
    expect(shown).not.toContain(line);
    expect(shown).not.toContain('Placed and returned');
    expect(shown).toContain('its outcome, duration and evidence are in History');
    expect(words({}, { ...view, data_mode: 'live' as const })).not.toContain('Live. Placed and returned.');
  });

  it('says what the rehearsal recorded, and never "placed", anywhere on the main screen', () => {
    const view = { ...liveView(), data_mode: 'synthetic' as const };
    const shown = words({ mission: rehearsal, sourceKind: 'live' }, view);
    expect(shown).toContain('Rehearsal: 2 of 2 done');
    expect(shown).not.toContain('placed');
    expect(shown).not.toContain('Placed');
    expect(shown).not.toContain('Mission time');
  });
});

describe('a stored frame in a camera panel', () => {
  const still = { observation_id: 'obs-1', source_role: 'wrist', media: 'frame', seq: 5, t_wall_utc: '2026-09-23T10:00:00Z' } as unknown as ObservationMeta;
  const withStill = (dataMode: ViewState['data_mode']): StoreSnapshot => {
    const store = new ConsoleStore(new SyntheticSource());
    return { ...store.getSnapshot(), view: { ...liveView(), data_mode: dataMode, observations: { 'obs-1': still } } };
  };
  const source = { ...new SyntheticSource(), previewUrl: () => null, frameUrl: (id: string) => `/api/frame/${id}` } as unknown as ConsoleSource;

  // DELIBERATELY CHANGED. This case pinned a SIMULATION runtime's stored frame being drawn in the camera panel under a
  // GENERATED caption. The design review's ruling ("gate stills on data_mode live, and label generated frames GENERATED
  // in Evidence only") and the operator's requirement ("generated rehearsal images appear only in Evidence with a
  // GENERATED label") take it out of the panel: a rehearsal's panel draws no stored frame and its cover says there is
  // no camera and points to Evidence. A live runtime's saved frame is unchanged, and so is a replay's absence.
  it('is drawn only on a live runtime; a SIMULATION panel draws none and points to Evidence, and a replay draws none', () => {
    const synthetic = cameraPanel(withStill('synthetic'), 'wrist', source, Date.now());
    expect(synthetic.stillUrl).toBeNull();
    expect(synthetic.stillKind).toBeNull();
    expect(synthetic.cover?.title).toBe('No camera in a rehearsal');
    expect(synthetic.cover?.note).toContain('Evidence');
    expect(synthetic.cover?.note).toContain('GENERATED');
    expect(cameraPanel(withStill('live'), 'wrist', source, Date.now()).stillKind).toBe('saved');
    const replay = cameraPanel(withStill('replay'), 'wrist', source, Date.now());
    expect(replay.stillUrl).toBeNull();
    expect(replay.cover).not.toBeNull();
  });

  // THE WHOLE SCREEN, not only the model: no picture element of a stored frame is rendered in any camera panel of a
  // synthetic runtime, whatever the fold holds. The live case beside it is the control, so this cannot pass by the
  // source simply having no frames to give.
  it('renders no <img> of a stored frame in a camera panel for a synthetic runtime', () => {
    const framed = {
      kind: 'live',
      label: 'a source that serves stored frames',
      previewUrl: () => null,
      frameUrl: (id: string) => `/api/observations/${id}/frame?what=raw`,
    } as unknown as ConsoleSource;
    const render = (dataMode: ViewState['data_mode']): string => {
      const store = new ConsoleStore(framed);
      const state = store.getSnapshot();
      (store as unknown as { snapshot: StoreSnapshot }).snapshot = {
        ...state,
        sourceKind: 'live',
        view: { ...liveView(), data_mode: dataMode, observations: { 'obs-1': still } },
      };
      return renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
    };
    const synthetic = render('synthetic');
    expect(synthetic).not.toContain('/api/observations/obs-1/frame');
    expect(synthetic).not.toContain('data-camera-still');
    expect(synthetic.match(/<img/g)).toBeNull();
    expect(text(synthetic)).toContain('No camera in a rehearsal');
    const live = render('live');
    expect(live).toContain('data-camera-still');
    expect(live).toContain('/api/observations/obs-1/frame');
    expect(text(live)).toContain('STALE · Saved observation');
  });
});

describe('the Evidence view of a rehearsal run', () => {
  it('says what the rehearsal recorded and never words it as a placement', async () => {
    const { Drawer } = await import('./Drawers');
    const runFinishedFixture = (await import('@kiwi/demo-contracts/fixtures/event/run.finished.json')).default;
    const { applyEvent } = await import('../model/reducer');
    const view = applyEvent(
      applySnapshot(initialState(), runningSnapshot as unknown as Snapshot),
      runFinishedFixture as unknown as Parameters<typeof applyEvent>[1],
    );
    const store = new ConsoleStore(new SyntheticSource());
    const snapshot = { ...store.getSnapshot(), view: { ...view, data_mode: 'synthetic' as const } };
    const markup = renderToStaticMarkup(
      createElement(StoreContext, { value: store }, createElement(Drawer, { id: 'evidence', snapshot, store, mode: 'synthetic', onSelect: () => {}, onClose: () => {} })),
    );
    const shown = text(markup);
    expect(shown).toContain("The rehearsal's run ended");
    expect(shown).toContain('nothing was physically moved by it');
    expect(shown).not.toContain('Placed and returned');
  });
});

describe("a rehearsal's generated decision frame, across the whole Diagnostics drawer", () => {
  // The Connections view used to carry the engineering reference's own copy of the decision frame, with only the
  // panel's watermark on it. The requirement is Evidence ONLY, under the generated label, so every other view of the
  // drawer is rendered here and none may hold a picture of it.
  const pinned = {
    observation_ids: ['obs-gen-0013'],
    primary_observation_id: 'obs-gen-0013',
    consumed_by: { by: 'predicate' as const, name: 'release_margin', seq: 50 },
    perception: null,
    capture: null,
    pinned_at_seq: 50,
  };
  const serving = {
    kind: 'live',
    label: 'a runtime that serves stored frames',
    previewUrl: () => null,
    frameUrl: (id: string, what: string, width: number) => `/api/observations/${id}/frame?what=${what}&w=${String(width)}`,
  } as unknown as ConsoleSource;

  async function drawer(id: 'history' | 'logs' | 'evidence' | 'connections', dataMode: ViewState['data_mode']): Promise<string> {
    const { Drawer } = await import('./Drawers');
    const store = new ConsoleStore(serving);
    const snapshot = { ...store.getSnapshot(), view: { ...liveView(), data_mode: dataMode, decision_frame: pinned } } as StoreSnapshot;
    return renderToStaticMarkup(
      createElement(StoreContext, { value: store }, createElement(Drawer, { id, snapshot, store, mode: dataMode, onSelect: () => {}, onClose: () => {} })),
    );
  }

  it('is drawn in Evidence under the generated label, in sentence case, and in no other view', async () => {
    const evidence = await drawer('evidence', 'synthetic');
    expect(evidence).toContain('/api/observations/obs-gen-0013/frame');
    expect(text(evidence)).toContain('Generated by the rehearsal, not a camera image');
    expect(text(evidence)).not.toContain('GENERATED');
    for (const id of ['history', 'logs', 'connections'] as const) {
      const markup = await drawer(id, 'synthetic');
      expect(markup, id).not.toContain('/api/observations/obs-gen-0013/frame');
      expect(markup.match(/<img/g), id).toBeNull();
    }
  });

  it('keeps Connections free of the frame for a live runtime too: the decision frame is drawn once, in Evidence', async () => {
    const connections = await drawer('connections', 'live');
    expect(connections).not.toContain('/api/observations/obs-gen-0013/frame');
    expect(text(connections)).toContain('What the controller looked at is in Evidence');
    expect(await drawer('evidence', 'live')).toContain('/api/observations/obs-gen-0013/frame');
  });
});
