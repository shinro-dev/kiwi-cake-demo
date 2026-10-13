// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE DEMO SCREEN, RENDERED (docs/autonomous-demo/console-redesign-2026-09-27.md). The stages are rendered on their own
// for each failure shape the fixtures carry (a lost block in ALIGN, three empty closes, a placed run), and then the whole
// screen is rendered and read: its six regions, every safety mark (the mode, a runtime that stopped answering, the
// uncertain-command block, the motion hold) and none of the labels that moved behind Diagnostics.
//
// A .ts file building elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { Capability } from '@kiwi/demo-contracts';
import { SyntheticSource } from '../api/synthetic';
import { readiness } from '../model/readiness';
import { ARM_HINT, readyChecklist } from '../model/readyChecklist';
import { motionPermissionView, robotLink } from '../model/robotStatus';
import { robotStatus } from '../model/robotStatusFixture';
import { alignLostRun, graspMissedRun, placedRun } from '../model/stageFixtures';
import { stageTimeline } from '../model/stageTimeline';
import type { ViewState } from '../model/types';
import type { ConsoleSource } from '../api/types';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { DemoScreen, StartChecks, timelineTarget, WristThumb } from './DemoScreen';
import { StageTimeline } from './StageTimeline';
import { C } from './theme';
import { TopBar } from './TopBar';
import { modeMark } from '../model/modeMark';
import type { CameraPanelModel } from './cameras';

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

function capability(id: string): Capability {
  return { id, state: 'enabled', reason: null, reason_code: null } as unknown as Capability;
}

const SHAPES: { name: string; view: () => ViewState; stage: string; failed: boolean }[] = [
  { name: 'a block lost in the ALIGN descent', view: () => alignLostRun('r'), stage: 'pick', failed: true },
  { name: 'three empty closes', view: () => graspMissedRun('r'), stage: 'pick', failed: true },
  { name: 'a placed run', view: () => placedRun('r'), stage: 'place', failed: false },
];

function storeShowing(view: ViewState, patch: Partial<StoreSnapshot> = {}): ConsoleStore {
  const store = new ConsoleStore(new SyntheticSource());
  // No injection point exists for the published snapshot, and adding one to production code for a test is the wrong
  // trade, so the test writes it directly, as the classic screen's render test does.
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view };
  return store;
}

function renderScreen(store: ConsoleStore): string {
  return text(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen))));
}

describe.each(SHAPES)('the stages for $name', ({ view, stage, failed }) => {
  it('draws the five stages, each with its word, and the stage the run ended in with its own word', () => {
    const folded = { ...view(), capabilities: [capability('return_arm'), capability('observe')] };
    const timeline = stageTimeline([{ run_id: 'r', view: folded }]);
    const drawn = renderToStaticMarkup(createElement(StageTimeline, { timeline, referenceDay: '2026-09-27' }));
    const words = text(drawn);
    for (const label of ['Find', 'Pick', 'Carry', 'Place', 'Return']) expect(words).toContain(label);
    expect(drawn).toContain(`data-stage="${stage}"`);
    if (failed) expect(words).toContain('Failed here');
    // The numbers, frames and rules a stage decided on are Evidence's, and no stage carries Cake text (spec 7.2).
    expect(drawn).not.toContain('data-stage-frame');
    expect(drawn).not.toContain('data-stage-number');
    expect(drawn).not.toContain('Cake');
    expect(drawn).not.toContain('text-transform');
  });
});

describe('a stage the run entered and left with no verifying predicate', () => {
  it('says "Entered" in the mute tone, never a warning and never "not verified", beside the stages that verified', () => {
    const timeline = stageTimeline([{ run_id: 'r', view: placedRun('r') }]);
    const drawn = renderToStaticMarkup(createElement(StageTimeline, { timeline, referenceDay: '2026-09-27' }));
    const entered = [...drawn.matchAll(/<li data-stage="([a-z]+)" data-stage-status="entered"[^>]*>(.*?)<\/li>/g)];
    expect(entered.map((match) => match[1])).toEqual(['find', 'carry']);
    for (const match of entered) {
      const row = match[2] ?? '';
      expect(/data-stage-verdict="" style="[^"]*color:([^;"]+)[^"]*">([^<]*)</.exec(row)?.slice(1)).toEqual([C.mute, 'Entered']);
      expect(row).not.toContain(C.warn);
      expect(row).not.toContain(C.bad);
    }
    expect(text(drawn)).not.toContain('not verified');
    expect(drawn).toContain('data-stage="place" data-stage-status="verified"');
  });
});

describe('the time on a stage row (verifier r2 defect 9)', () => {
  it('says the time is when the run entered the stage, never a bare time that reads as the stop', () => {
    const run = placedRun('r');
    const events = run.recent_events.map((event, index) => ({ ...event, t_wall_utc: `2026-09-27T14:22:${String(10 + index).padStart(2, '0')}.000Z` }));
    const timeline = stageTimeline([{ run_id: 'r', view: run, events }]);
    const drawn = renderToStaticMarkup(createElement(StageTimeline, { timeline, referenceDay: '2026-09-27' }));
    const times = [...drawn.matchAll(/<span data-t="[^"]+" title="([^"]+)"[^>]*>([^<]*)<\/span>/g)];
    expect(times.length).toBeGreaterThan(0);
    for (const match of times) {
      expect(match[2]).toMatch(/^entered \d{2}:\d{2}:\d{2}Z$/);
      expect(match[1]).toContain('entered this stage');
    }
  });
});

describe('the readiness detail in the top bar', () => {
  it('lists the arm and the wrist under "Not checked by the runtime", and they never change the verdict', () => {
    const status = robotStatus();
    const read = { status, error: null, readAtMs: 1000 };
    const checklist = readyChecklist({
      view: { ...alignLostRun('r'), data_mode: 'live', commands_enabled: true, snapshot_at_ms: 1000 },
      sourceKind: 'live',
      sourceError: null,
      replaying: false,
      nowMs: 1500,
      referenceDay: '1970-01-01',
      robot: status,
      link: robotLink(read, 1500),
      permission: motionPermissionView(read, 1500),
      unresolved: null,
      wrist: { showing: false, note: 'preview_not_configured' },
      startOff: null,
    });
    const verdict = readiness({ mission: null, runActive: false, startOff: null, checklist: checklist.items });
    expect(verdict.verdict.id).toBe('ready');
    const markup = renderToStaticMarkup(
      createElement(TopBar, {
        mode: modeMark({ dataMode: 'live', sourceKind: 'live', replaying: false }),
        robot: { label: 'LeKiwi bench', connection: 'connected', tone: 'ok', notReadSince: null },
        motion: null,
        readiness: verdict,
        start: { label: 'Start', off: null, busy: false },
        stop: { off: null, busy: false },
        onStart: () => undefined,
        onStop: () => undefined,
        onDiagnostics: () => undefined,
      }),
    );
    const words = text(markup);
    expect(words).toContain('Not checked by the runtime');
    expect(words).toContain('Arm acquired:');
    expect(words).toContain('Wrist view:');
    // Each item's hint is said beside it: what the operator does about it (afeab8f demoScreen.test.ts:146).
    expect(words).toContain('acquire the arm with the operator tooling');
    const armItem = /<li[^>]*data-ready-item="arm"[^>]*>([\s\S]*?)<\/li>/.exec(markup)?.[1] ?? '';
    expect(armItem).toContain('data-ready-hint');
    expect(text(armItem)).toContain(ARM_HINT);
  });
});

describe('the wrist thumbnail', () => {
  const panel = {
    role: 'wrist', word: 'STALLED', tone: '#fbbf24', chipBg: 'transparent', note: 'no_sample_within_window; the runtime last received a frame 3.2 s ago',
    streamUrl: null, stillUrl: null, still: null, cover: null, fps: null, deliveredFps: null,
    measuringRate: false, lastLineAgeMs: null, lastFrameAgeMs: null, stillRecordedAgeMs: 474000, stillRecordedAt: null, stillKind: null,
    showingVideo: false, attempt: null,
  } as CameraPanelModel;

  it('says one state word and one line, with the runtime reason and no age', () => {
    const drawn = renderToStaticMarkup(createElement(WristThumb, { panel, binding: null, controls: null, dimmed: false }));
    const words = text(drawn);
    expect(words).toContain('Stalled');
    expect(words).toContain('no_sample_within_window');
    expect(words).not.toMatch(/\bago\b/);
    expect(drawn).not.toContain('text-transform');
  });

  it('keeps the last picture this page decoded through the gap, at a fixed time that does not tick', () => {
    const kept = { url: 'data:image/jpeg;base64,AAAA', at_ms: Date.parse('2026-09-27T14:21:46.250Z') };
    const draw = (): string => renderToStaticMarkup(createElement(WristThumb, { panel: { ...panel, stillUrl: '/api/cameras/wrist/still.jpg' }, binding: null, controls: null, dimmed: false, kept, referenceDay: '2026-09-27' }));
    vi.useFakeTimers();
    try {
      vi.setSystemTime(Date.parse('2026-09-27T14:21:50Z'));
      const first = draw();
      vi.setSystemTime(Date.parse('2026-09-27T14:22:10Z'));
      const second = draw();
      expect(first).toContain('data-kept-picture');
      expect(text(first)).toContain("The last picture this page decoded, at 14:21:46Z (this display's clock); not live.");
      expect(first).toContain('data-t="2026-09-27T14:21:46.250Z"');
      expect(text(first)).not.toMatch(/\bago\b/);
      // The kept picture is drawn instead of a saved frame, so no line describes a picture that is not drawn.
      expect(first).not.toContain('data-camera-still');
      expect(text(first)).not.toContain('The newest saved frame');
      expect(text(first)).toContain('no_sample_within_window');
      expect(second).toBe(first);
    } finally {
      vi.useRealTimers();
    }
  });

  it('draws no kept picture while the stream shows video', () => {
    const kept = { url: 'data:image/jpeg;base64,AAAA', at_ms: Date.parse('2026-09-27T14:21:46.250Z') };
    const drawn = renderToStaticMarkup(createElement(WristThumb, { panel: { ...panel, showingVideo: true }, binding: null, controls: null, dimmed: false, kept, referenceDay: '2026-09-27' }));
    expect(drawn).not.toContain('data-kept-picture');
  });
});

describe('the whole demo screen', () => {
  it('carries the top bar controls and the six regions, each named', () => {
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(alignLostRun('r')) }, createElement(DemoScreen)));
    const words = text(markup);
    for (const label of ['Start', 'Stop', 'Diagnostics', 'Scan scene', 'Observe scene', 'Find', 'Pick', 'Carry', 'Place', 'Return']) expect(words).toContain(label);
    for (const region of ['mission', 'cake', 'timeline', 'stages', 'scene', 'wrist']) expect(markup).toContain(`data-region="${region}"`);
    for (const heading of ['Mission', 'Cake on the robot', 'Timeline', 'Stages', 'Scene', 'Wrist camera']) expect(words).toContain(heading);
    expect(markup.match(/data-control="stop"/g)?.length).toBe(1);
    expect(markup).not.toContain('text-transform');
  });

  it('carries every safety mark: the mode, a runtime that stopped answering, the uncertain-command block and the motion hold', () => {
    const unresolved: StoreSnapshot['liveUnresolved'] = {
      command_id: 'abcd1234ef0123456789',
      run_id: 'sc-20260101T000000Z-abcd1234',
      command_type: 'start_cycle',
      reason_code: 'runtime_restarted_before_child_recorded',
      classification: 'ran_and_was_interrupted',
      block_reason_code: 'owner_unresolved',
      question: 'confirm that none is running and say how it ended, after looking at the robot',
      findings: ['no_controller_running', 'controller_finished', 'controller_stopped_by_operator'],
      evidence: {
        process: { alive: false, readable: true, found_by: null },
        run_files: { readable: true, terminal_record: false, event_records: 41, output_present: true, outcome: null },
        observations: { checked: true, readable: true, files_after_accept: 3 },
        journal: { status: 'uncertain', reason_code: 'runtime_restarted_before_child_recorded', child_identity: true },
        unreadable: [],
      },
      attestations: [],
      blocked_commands: ['start_cycle', 'observe'],
    };
    const markup = renderToStaticMarkup(
      createElement(
        StoreContext,
        {
          value: storeShowing(alignLostRun('r'), {
            liveUnresolved: unresolved,
            sourceError: 'the runtime did not answer',
            links: [{ n: 1, at: '2026-09-27T14:30:02.000Z', lost: true }],
            robot: robotStatus(),
            robotReadAtMs: Date.now(),
          }),
        },
        createElement(DemoScreen),
      ),
    );
    const words = text(markup);
    expect(markup).toContain('data-mode-mark="rehearsal"');
    expect(markup).toContain('data-readiness="not_ready"');
    expect(words).toContain('The runtime is not answering.');
    expect(words).toContain('a command whose execution is NOT known');
    // The motion hold: the fixture's robot answers with motion held because no session was confirmed.
    expect(words).toContain('Robot motion Held');
  });

  it('carries none of the labels that moved behind Diagnostics, and none of the deleted ones', () => {
    const words = renderScreen(storeShowing(placedRun('p')));
    for (const cut of ['Delivery metadata', 'Current action', 'Latest ', 'last line', 'fps delivered', 'Live demonstration', 'LIVE RUNTIME', 'PHYSICAL', 'SYNTHETIC', 'What changed in this mission', 'Last check']) {
      expect(words).not.toContain(cut);
    }
  });
});

describe('the target the stages are drawn for while the active target has no run yet', () => {
  const twoTargets: NonNullable<StoreSnapshot['mission']> = {
    mission_record_version: '1', mission_id: 'mission-0001', state: 'RUNNING', data_mode: 'synthetic', performs_motion: false,
    execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-2', waiting_reason: null, detail: null,
    targets: [
      { target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: false, state: 'FAILED', runs: ['r'] },
      { target_id: 'block-2', label: 'Block 2', reachable: true, requires_capability: false, state: 'ACTIVE', runs: [] },
    ],
    activations: [],
  };

  it('names the target the drawn run belongs to, not the active target it does not belong to', () => {
    expect(timelineTarget(twoTargets, 'r')?.target_id).toBe('block-1');
    // With no run on the screen the active target is named.
    expect(timelineTarget(twoTargets, null)?.target_id).toBe('block-2');
    // A run the mission recorded against no target is drawn as the run on the screen.
    expect(timelineTarget(twoTargets, 'elsewhere')).toBeNull();
  });
});

describe('the demo screen while the mission is held for the operator after a failure', () => {
  it('says Held for you once, in the mission card, and none of the capability-wait sentences', () => {
    const mission: StoreSnapshot['mission'] = {
      mission_record_version: '1', mission_id: 'mission-0001', state: 'WAITING_FOR_CAPABILITY', data_mode: 'synthetic', performs_motion: false,
      execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-1', waiting_reason: 'operator_decision', detail: null,
      targets: [{ target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY', runs: ['r'] }],
      activations: [],
    };
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(alignLostRun('r'), { mission, missionError: null }) }, createElement(DemoScreen)));
    const words = text(markup);
    expect(markup).toContain('data-verdict="held_for_you"');
    expect(words).toContain('Mission held for you');
    for (const claim of ['Waiting for Cake', 'Nothing has failed', 'held here for the base capability', 'Activate a candidate', 'the runtime gives the reason operator_decision']) {
      expect(words).not.toContain(claim);
    }
  });
});

describe('the Return arm step after the console is opened on a finished run (restores afeab8f demoScreen.test.ts:222)', () => {
  it('draws Return arm on from the live list, never off from the read-back fold, with the run hydrated through the store', async () => {
    // A reload after a failed run: the live fold carries the outcome but no run.started, so the store reads the run back
    // (`hydrateRun`) and folds it from the initial state, whose capability list is empty. The runtime's live list offers
    // the control, and the step's button must say so.
    const run = alignLostRun('r');
    const live = { ...run, run_started: null, capabilities: [capability('return_arm')] };
    const base = new SyntheticSource();
    const source = new Proxy(base, {
      get(target, key) {
        if (key === 'historyEvents') return async () => ({ ok: true, run_id: 'r', events: run.recent_events, origins: [] });
        const value: unknown = Reflect.get(target, key, target);
        return typeof value === 'function' ? (value as (...args: unknown[]) => unknown).bind(target) : value;
      },
    });
    const store = new ConsoleStore(source, { storage: null });
    (store as unknown as { liveView: ViewState }).liveView = live;
    const mission: StoreSnapshot['mission'] = {
      mission_record_version: '1', mission_id: 'mission-0001', state: 'FAILED', data_mode: 'synthetic', performs_motion: false,
      execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-1', waiting_reason: null, detail: null,
      targets: [{ target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: 'auto', state: 'FAILED', runs: ['r'] }],
      activations: [], next_actions: ['return_arm', 'start'],
    };
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...store.getSnapshot(), view: live, mission, missionError: null };
    await store.hydrateRun('r');
    const hydrated = store.getSnapshot().hydrated;
    expect(hydrated?.run_id).toBe('r');
    expect(hydrated?.view.capabilities).toEqual([]);
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
    expect(markup).toContain('data-step="return_arm"');
    expect(markup).toMatch(/data-control="return_arm"[^>]*aria-disabled="false"/);
    expect(markup).not.toMatch(/data-control="return_arm"[^>]*aria-disabled="true"/);
  });
});

describe('the Start dialog', () => {
  it('says where the confirmations go, lists each check and each step by hand, and keeps the attach paragraph behind "why"', () => {
    const lines = ['I am at the robot and the servo power switch is within reach.', 'Open the gripper by hand and take the block out.'];
    const markup = renderToStaticMarkup(createElement(StartChecks, { lines, checked: [false, false], onChange: () => undefined }));
    const words = text(markup);
    expect(words).toContain("Your confirmations are kept in this display's press list. The runtime does not receive them.");
    for (const line of lines) expect(words).toContain(line);
    expect(words).not.toContain('The record will say attested');
    expect(words).toContain('The arm has to be acquired after every power cycle; this console cannot read whether it is.');
    expect(markup).toMatch(/<details[^>]*><summary[^>]*>why<\/summary>/);
    expect(words).toContain('the safe-attach host refuses while torque is off');
  });
});

describe('Try again beside a mission read that is not current (spec 4.3, Stale)', () => {
  it('is drawn off while the last mission read failed, and on while it is current', () => {
    const mission: StoreSnapshot['mission'] = {
      mission_record_version: '1', mission_id: 'mission-0001', state: 'WAITING_FOR_CAPABILITY', data_mode: 'synthetic', performs_motion: false,
      execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-1', waiting_reason: 'operator_decision', detail: null,
      targets: [{ target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY', runs: ['r'] }],
      activations: [],
      next_actions: ['retry', 'stop'],
      hold: { target_id: 'block-1', attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN', observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['retry', 'stop'], budget_left: 1, retry_requested: false },
    };
    const draw = (missionError: string | null): string =>
      renderToStaticMarkup(createElement(StoreContext, { value: storeShowing(alignLostRun('r'), { mission, missionError, missionReadAtMs: Date.now() }) }, createElement(DemoScreen)));
    expect(draw('the runtime did not answer')).toMatch(/data-control="retry"[^>]*aria-disabled="true"/);
    expect(draw('the runtime did not answer')).toContain('data-stale-marker="mission"');
    // The same hold read current, retry listed and the source offering the retry door: Try again is on.
    expect(storeShowing(alignLostRun('r'), { mission, missionError: null, missionReadAtMs: Date.now() }).retryOffered).toBe(true);
    expect(draw(null)).toMatch(/data-control="retry"[^>]*aria-disabled="false"/);
    expect(draw(null)).not.toContain('data-stale-marker="mission"');
  });
});

describe('Try again on a runtime that offers no mission retry (spec 9: no change to what a control admits)', () => {
  it('is drawn off with "this runtime does not offer a mission retry" when the source has no retry door', () => {
    const mission: StoreSnapshot['mission'] = {
      mission_record_version: '1', mission_id: 'mission-0001', state: 'WAITING_FOR_CAPABILITY', data_mode: 'synthetic', performs_motion: false,
      execution_label: 'recording-rehearsal', started_wall_utc: null, selected_target: 'block-1', waiting_reason: 'operator_decision', detail: null,
      targets: [{ target_id: 'block-1', label: 'Block 1', reachable: true, requires_capability: 'auto', state: 'WAITING_FOR_CAPABILITY', runs: ['r'] }],
      activations: [],
      next_actions: ['retry', 'stop'],
      hold: { target_id: 'block-1', attempt: 1, run_id: 'r', park_run_id: null, outcome_class: 'retryable', reason_code: 'align_cube_not_detected', phase: 'ALIGN', observation_ids: [], stop_state: { holding: false, base_ahead: false, moved: true }, next_actions: ['retry', 'stop'], budget_left: 1, retry_requested: false },
    };
    // The synthetic source with its retry door masked by an own property that is not a function (store/retry.test.ts).
    const base = new SyntheticSource();
    const noRetry = Object.assign(Object.create(Object.getPrototypeOf(base) as object) as ConsoleSource, base, { missionRetry: undefined });
    const store = new ConsoleStore(noRetry);
    const held = store.getSnapshot();
    (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, mission, missionError: null, missionReadAtMs: Date.now(), view: alignLostRun('r') };
    expect(store.retryOffered).toBe(false);
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(DemoScreen)));
    expect(markup).toMatch(/data-control="retry"[^>]*aria-disabled="true"/);
    expect(markup).toMatch(/data-control="retry"[^>]*title="[^"]*this runtime does not offer a mission retry/);
  });
});
