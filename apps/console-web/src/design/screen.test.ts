// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE PORTED SCREEN, AND THE THREE FIGURES THE DESIGN ADDED.
//
// Two kinds of test live here. The first covers the pure model the port needed and this console did not have: mission
// time, target and progress, the per-role camera panel, the Cake reading and the history line. Every honesty rule the
// port introduces is in one of those functions, so that is where they are pinned.
//
// The second RENDERS THE WHOLE SCREEN to static markup and reads the words out. That is here because of a defect this
// project has already had: `OutcomePanel.tsx` was written, reviewed and committed while nothing imported it, and no
// test in this package rendered a component, so the whole suite stayed green over a panel that was never on the
// screen. A render test is the cheapest thing that cannot be fooled that way.
//
// The last case is the one that matters most for a design port: the imported document is a PROTOTYPE, with two cubes,
// an invented module inventory and a placement measurement that was made up for the walkthrough. Those strings are
// asserted ABSENT. A port that quietly kept one of them would look perfect and be a lie.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Snapshot } from '@kiwi/demo-contracts';
import runningSnapshot from '@kiwi/demo-contracts/fixtures/snapshot/running.json';
import runStarted from '@kiwi/demo-contracts/fixtures/event/run.started.json';
import runFinished from '@kiwi/demo-contracts/fixtures/event/run.finished.json';
import runFailed from '@kiwi/demo-contracts/fixtures/event/run.finished.failed-with-a-reason.json';
import notApplicable from '@kiwi/demo-contracts/fixtures/event/predicate.evaluated.not-applicable-in-this-phase.json';
import type { ContractEvent } from '../contracts/envelope';
import type { ConsoleSource, PreviewSample } from '../api/types';
import { SyntheticSource } from '../api/synthetic';
import { applyEvent, applySnapshot, initialState } from '../model/reducer';
import type { OutcomeView, PredicateView, ViewState } from '../model/types';
import { establishedResult } from '../model/acceptance';
import { missionProgress, missionTarget, missionTime } from '../model/mission';
import { ConsoleStore } from '../store/store';
import type { StoreSnapshot } from '../store/store';
import { StoreContext } from '../store/useConsole';
import { cakeModel, cakeTaskSentence } from './cake';
import { CAKE_TASK_SENTENCE } from '../model/cakeTask';
import type { MissionRecord } from '../model/missionRecord';
import { cameraPanel } from './cameras';
import { decoded, firstAttempt } from '../model/streamDecode';
import { lineOf } from './timeline';
import { Screen } from './Screen';
import { C } from './theme';
import { RESULT_RULE_COLOUR, resultFailed, resultRule, resultRuleColour } from './resultRule';

const STARTED_AT = Date.parse('2026-01-01T00:00:00Z');

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

/** The running fixture, which carries an active run, four components, four capabilities and the Cake ladder. */
function running(): ViewState {
  return applySnapshot(initialState(), runningSnapshot as unknown as Snapshot);
}

/** The same, for a tab that folded the record that STARTED the run, which is what dates it. */
function runningFromTheStart(): ViewState {
  return applyEvent(running(), runStarted as unknown as ContractEvent);
}

/** A source that answers for both cameras and for stored frames, so a panel can be tested without a server. */
function source(previewRoles: string[]): ConsoleSource {
  return {
    ...new SyntheticSource(),
    previewUrl: (role: string) => (previewRoles.includes(role) ? `/api/preview.mjpg?role=${role}` : null),
    frameUrl: (observationId: string) => `/api/frame/${observationId}`,
  } as unknown as ConsoleSource;
}

function sample(patch: Record<string, unknown>): PreviewSample {
  return { state: 'connected', available: true, measured_fps: 7.9, seq: 12, ...patch } as unknown as PreviewSample;
}

function storeShowing(view: ViewState, patch: Partial<StoreSnapshot> = {}): ConsoleStore {
  const store = new ConsoleStore(new SyntheticSource());
  // The store's published snapshot is what every component reads. There is no injection point for it, and adding one
  // to production code for a test's benefit would be the wrong trade, so the test writes it directly and says so.
  const held = store.getSnapshot();
  (store as unknown as { snapshot: StoreSnapshot }).snapshot = { ...held, ...patch, view };
  return store;
}

describe('mission time', () => {
  it('is unavailable, and says why, for a tab that joined after the run began', () => {
    // The snapshot names an active run but this fold never saw it start, so nothing here dates it. A zero would be a
    // duration nobody measured.
    const time = missionTime(running(), STARTED_AT + 26_000);
    expect(time.words).toBeNull();
    expect(time.meaning).toContain('joined after the run began');
    expect(time.running).toBe(false);
  });

  it('counts from the run.started record for a tab that folded it', () => {
    const time = missionTime(runningFromTheStart(), STARTED_AT + 26_000);
    expect(time.words).toBe('00:26');
    expect(time.running).toBe(true);
    // The two clocks are on different machines and the screen says so rather than implying one reading.
    expect(time.meaning).toContain('different machines');
  });

  it('carries hours rather than wrapping silently at sixty minutes', () => {
    expect(missionTime(runningFromTheStart(), STARTED_AT + 3_661_000).words).toBe('1:01:01');
  });
});

// THE TERMINAL TIMER. A tab that watched a whole run had both readings and used neither: the reducer dropped the
// envelope's wall time on run.finished, so mission time took the still-running branch and counted up after the run
// had ended. The freeze has to hold for a run that FAILED as much as for one that placed.
describe('mission time stops at the recorded end of the run', () => {
  const ended = (finished: Record<string, unknown>, atMs: number) => {
    const withEnd = { ...finished, run_id: 'synthetic-run-0001', t_wall_utc: new Date(STARTED_AT + 83_000).toISOString() };
    return missionTime(applyEvent(runningFromTheStart(), withEnd as unknown as ContractEvent), atMs);
  };

  it('freezes at the duration the runtime recorded, however long the tab stays open', () => {
    const atEnd = ended(runFinished, STARTED_AT + 83_000);
    expect(atEnd.words).toBe('01:23');
    expect(atEnd.running).toBe(false);
    // An hour later it still says the same thing, because it is a duration and not a clock.
    expect(ended(runFinished, STARTED_AT + 3_683_000).words).toBe('01:23');
  });

  it('freezes for a run that FAILED, not only for one that placed', () => {
    const failed = ended(runFailed, STARTED_AT + 999_000);
    expect(failed.words).toBe('01:23');
    expect(failed.running).toBe(false);
  });

  it('states that both readings are the runtime own clock', () => {
    expect(ended(runFinished, STARTED_AT + 83_000).meaning).toContain("runtime's own wall clock");
  });

  it('still says unavailable for a tab that joined after the run began', () => {
    // A late join folded no run.started, so it has no start to measure from and must not invent one.
    const late = missionTime(applyEvent(running(), { ...runFinished, run_id: 'synthetic-run-0001' } as unknown as ContractEvent), STARTED_AT + 83_000);
    expect(late.words).toBeNull();
    expect(late.running).toBe(false);
    expect(late.meaning).toContain('joined after the run began');
  });
});

describe('progress over the one cube', () => {
  it('counts one cube and never two', () => {
    expect(missionProgress(runningFromTheStart()).words).toBe('0 of 1 cube placed');
  });

  it('refuses to say zero for a tab that simply did not watch', () => {
    // The same distinction `model/phase.ts` draws with not_observed. An absent placement record in a tab that joined
    // mid-run is this tab's ignorance, not a measurement of nothing having been placed.
    const progress = missionProgress(running());
    expect(progress.placed).toBeNull();
    expect(progress.words).toContain('not seen from here');
  });
});

describe('the target', () => {
  it('names where the one cube has got to, not which cube it is', () => {
    const target = missionTarget(runningFromTheStart());
    expect(target.label).toBe('not located yet');
    expect(target.basis).toContain('no perception record');
  });
});

/** The picture element of the current attempt decoded a frame: the one basis for LIVE (`model/streamDecode.ts`). */
const DECODED = decoded(firstAttempt(900), 640, 950);

describe('each camera answers for itself', () => {
  // DELIBERATELY CHANGED in the two cases below that read `word` as LIVE: they now hand the panel a decode state. The
  // word used to follow the runtime's metadata alone, and the operator ruled that advancing metadata does not prove
  // visible video, so LIVE now needs the panel's own element to have decoded a frame. What these cases pin (each
  // camera answering for itself, and no negative age) is unchanged; that the metadata alone no longer says LIVE is
  // asserted in the first of them.
  it('does not let a streaming wrist speak for a silent front', () => {
    const view = running();
    const base = storeShowing(view).getSnapshot();
    const snapshot: StoreSnapshot = {
      ...base,
      telemetry: {
        ...base.telemetry,
        previews: { wrist: sample({ role: 'wrist' }) },
        preview_received_at_ms_by_role: { wrist: 1000 },
        // A STREAMING wrist has delivered frames, so the frame stamps say so. The runtime's own rate is only offered
        // as current while a frame has reached this tab inside the rate window; a fixture that records a line but no
        // frame is a stalled camera, which is the next test.
        preview_frame_at_ms_by_role: { wrist: 1000 },
        preview_frame_times_by_role: { wrist: [800, 900, 1000] },
      },
    };
    const wrist = cameraPanel(snapshot, 'wrist', source(['wrist', 'front']), 1200, DECODED);
    const front = cameraPanel(snapshot, 'front', source(['wrist', 'front']), 1200);

    expect(wrist.word).toBe('LIVE');
    expect(wrist.showingVideo).toBe(true);
    // THE SAME METADATA WITH NOTHING DECODED IS NOT LIVE.
    const undecoded = cameraPanel(snapshot, 'wrist', source(['wrist', 'front']), 1200);
    expect(undecoded.word).toBe('CONNECTING');
    expect(undecoded.showingVideo).toBe(false);
    expect(undecoded.note).not.toContain('advancing');
    expect(wrist.streamUrl).toContain('role=wrist');
    expect(wrist.fps).toBe(7.9);

    // The front camera has sent nothing. It says so under its own name and shows no picture at all.
    expect(front.word).toBe('NO LINE');
    expect(front.streamUrl).toBeNull();
    expect(front.cover).not.toBeNull();
    expect(front.fps).toBeNull();
  });

  // THE DEFECT THIS QUANTITY EXISTS FOR. The runtime heartbeats a frame-less status line about once a second while a
  // source is STALLED, and the browser stamped a preview receipt on every preview line. A camera that had stopped
  // delivering therefore read as "last line just now", at the rate it used to run, for as long as the runtime kept
  // answering about it.
  it('reports a stalled camera while the runtime keeps answering about it', () => {
    const base = storeShowing(running()).getSnapshot();
    const snapshot: StoreSnapshot = {
      ...base,
      telemetry: {
        ...base.telemetry,
        previews: { wrist: sample({ role: 'wrist' }) },
        // The line arrived a moment ago: the runtime is healthy and is still answering about this camera.
        preview_received_at_ms_by_role: { wrist: 60_000 },
        // The last FRAME arrived thirty seconds ago. Nothing since was a picture this tab had not counted.
        preview_frame_at_ms_by_role: { wrist: 30_000 },
        preview_frame_times_by_role: { wrist: [29_800, 29_900, 30_000] },
      },
    };
    const wrist = cameraPanel(snapshot, 'wrist', source(['wrist', 'front']), 60_100);

    expect(wrist.lastLineAgeMs).toBe(100);
    expect(wrist.lastFrameAgeMs).toBe(30_100);
    // The runtime's frozen rate is no longer offered as current, and this tab counts no delivered frames.
    expect(wrist.fps).toBeNull();
    expect(wrist.deliveredFps).toBeNull();
  });

  it('never reports a negative age for a line that arrived after the shared tick', () => {
    // The tick is read once a second and an arrival is stamped the instant the line lands, so an arrival stamp can be
    // NEWER than the reading a render is using. This was on the screen as "last line -325 ms ago".
    const base = storeShowing(running()).getSnapshot();
    const snapshot: StoreSnapshot = {
      ...base,
      telemetry: {
        ...base.telemetry,
        previews: { wrist: sample({ role: 'wrist' }) },
        preview_received_at_ms_by_role: { wrist: 1325 },
      },
    };
    const wrist = cameraPanel(snapshot, 'wrist', source(['wrist']), 1000, DECODED);
    expect(wrist.lastLineAgeMs).toBe(0);
    expect(wrist.word).toBe('LIVE');
  });

  it('reports a stalled camera as stalled rather than as live', () => {
    const base = storeShowing(running()).getSnapshot();
    const snapshot: StoreSnapshot = {
      ...base,
      telemetry: {
        ...base.telemetry,
        previews: { wrist: sample({ role: 'wrist', state: 'stalled', reason: 'no_sample_within_window' }) },
        preview_received_at_ms_by_role: { wrist: 1000 },
      },
    };
    const wrist = cameraPanel(snapshot, 'wrist', source(['wrist']), 1200);
    expect(wrist.word).toBe('STALLED');
    expect(wrist.streamUrl).toBeNull();
    expect(wrist.note).toContain('no_sample_within_window');
  });
});

describe('Cake is shown as it is', () => {
  it('reports the ladder the runtime published, with null as a third answer', () => {
    const cake = cakeModel(running());
    expect(cake.ladder).toHaveLength(5);
    const admin = cake.ladder.find((rung) => rung.key === 'admin_interface_compatible');
    // A rung the runtime did not establish is "not determined". Drawing it as "no" would state a finding nobody made.
    expect(admin?.words).toBe('not determined');
    expect(admin?.value).toBeNull();
    expect(cake.synthetic).toBe(true);
  });

  // THE PIN, MOVED DELIBERATELY. The task sentence was a constant, pinned here so that wiring up a mission could not
  // change it as a side effect. The one-block demo (docs/autonomous-demo/one-block-demo-2026-09-26.md, T4) is a
  // mission that WAITS on a Cake activation and RESUMES through one, and "Cake: not connected to this task" is false
  // for the whole of that mission. The sentence now reads what the mission record says, and the constant stays for a
  // runtime with no mission and for a mission that never touched Cake, which is what it was always true of.
  it('states the constant for no mission, and what the mission record says when it waited on or resumed through Cake', () => {
    expect(cakeModel(running()).taskSentence).toBe(CAKE_TASK_SENTENCE);
    expect(cakeTaskSentence(null)).toBe(CAKE_TASK_SENTENCE);
    const record = (extra: Partial<MissionRecord>): MissionRecord => ({
      mission_record_version: '1', mission_id: 'mission-0001', state: 'RUNNING', data_mode: 'live', performs_motion: true, execution_label: 'physical',
      started_wall_utc: null, selected_target: 'cube', waiting_reason: null, detail: null,
      targets: [{ target_id: 'cube', label: 'the block', reachable: true, requires_capability: 'auto', state: 'ACTIVE', runs: [] }],
      activations: [], ...extra,
    });
    const activation = (state: string): MissionRecord['activations'][number] => ({
      operation_uuid: `op-${state}`, target_id: 'cube', candidate_key: 'mobile-demo-swapped', payload_digest: null, activation_state: state,
      outcome: null, transaction_phase: null, transaction_uuid: null, detail: null, t_wall_utc: null,
    });
    // A mission that never touched Cake: the constant is true of it.
    expect(cakeModel(running(), null, null, record({})).taskSentence).toBe(CAKE_TASK_SENTENCE);
    // Held: the mission is waiting on a Cake activation.
    const held = cakeTaskSentence(record({ state: 'WAITING_FOR_CAPABILITY', waiting_reason: 'capability_unavailable' }));
    expect(held).toContain('Execution: Python baseline.');
    expect(held).toContain('held for a capability');
    expect(held).not.toContain('not connected');
    // Resumed: an activation was OBSERVED, and the mission went on through it.
    const resumed = cakeTaskSentence(record({ activations: [activation('REJECTED_BY_CAKE'), activation('OBSERVED')] }));
    expect(resumed).toContain('resumed through a Cake activation');
    expect(resumed).toContain('mobile-demo-swapped');
    expect(resumed).not.toContain('not connected');
    // Attempted and not observed: said as that, never as a resume and never as not connected.
    const attempted = cakeTaskSentence(record({ state: 'STOPPED', activations: [activation('REJECTED_BY_CAKE')] }));
    expect(attempted).toContain('1 activation was attempted');
    expect(attempted).toContain('none was observed');
    expect(attempted).not.toContain('resumed');
  });

  it('reports a component that states no version as stating none', () => {
    const rows = cakeModel(running()).modules;
    const host = rows.find((row) => row.id === 'robot-host');
    expect(host?.versionKnown).toBe(false);
    expect(host?.version).toBe('version not reported');
  });
});

describe('what a finished run is established to have done', () => {
  const predicate = (name: string, value: boolean | null, extra: Record<string, unknown> = {}) =>
    ({ predicate: name, value, state: 'evaluated', reason_code: null, rule_ref: null, rule_digest: null,
       threshold: null, measured: null, unit: null, evidence: [], consumed_source_stamp: null, measurements: [],
       seq: 1, t_mono_ns: 0, clock_domain: 'laptop_monotonic', ...extra }) as unknown as PredicateView;

  const outcome = (reported: string | null) => ({ reported, confirmed: null, predicates: null, run_id: 'r-1' } as unknown as OutcomeView);

  it('reads the placement from the retained predicates when the outcome word does not carry it', () => {
    // The preserved browser run: run.finished says `unknown`, and the run's own retained predicates say the cube was
    // placed and the base came back. The screen printed the single word "unknown" over a run that demonstrably did
    // its job. Decided from predicate values, so it is the same derivation for every run and names no run id.
    const established = establishedResult(outcome('unknown'), [
      predicate('placed', true),
      predicate('base_returned', true),
    ]);
    expect(established.headline).toBe('Placed and returned');
    expect(established.from_predicates).toBe(true);
    // The recorded word is KEPT, beside the headline, not instead of it and not rewritten.
    expect(established.recorded).toBe('unknown');
  });

  it('carries the clearance warning from the record that measured it', () => {
    const established = establishedResult(outcome('unknown'), [
      predicate('placed', true),
      predicate('base_returned', true),
      predicate('clearance_target_met', false, { measured: 35.57, threshold: 37.96, unit: 'mm' }),
    ]);
    expect(established.qualifier).toContain('Cube-centre edge margin: 35.57 mm');
    expect(established.qualifier).toContain('missed by 2.39 mm');
    // The wording rule this console already had: these two numbers are not a footprint clearance.
    expect(established.qualifier).not.toContain('footprint');
  });

  it('does not restate a word the measurements already say', () => {
    const established = establishedResult(outcome('placed_new'), [predicate('placed', true), predicate('base_returned', true)]);
    expect(established.headline).toBe('Placed and returned');
    expect(established.recorded).toBeNull();
  });

  it('falls back to the recorded word when no predicate establishes anything', () => {
    const established = establishedResult(outcome('stopped'), []);
    expect(established.headline).toBe('stopped');
    expect(established.from_predicates).toBe(false);
  });
});

describe('progress while the run evidence is still being read', () => {
  it('says it is reading rather than reporting this tab as the run', () => {
    // "not in this fold" described this tab's bookkeeping. The two states that matter are the one that becomes an
    // answer in a moment and the one that never will.
    const view = { ...runningFromTheStart(), active_run: null, outcome: { ...running().outcome, run_id: 'r-1', reported: 'unknown', finished_seq: 9 } } as ViewState;
    expect(missionProgress(view, [], 'reading').words).toContain('reading the run evidence');
    expect(missionProgress(view, [], 'unavailable').words).toContain('not recorded for this run');
    expect(missionProgress(view, [], 'unavailable').words).not.toContain('fold');
  });
});

describe('a history line', () => {
  it('does not turn a predicate that was not evaluated into one that measured false', () => {
    const line = lineOf(notApplicable as unknown as ContractEvent);
    expect(line.text).toContain('not applicable');
    expect(line.text).not.toContain('measured false');
    expect(line.tone).toBe('warn');
  });
});

describe('the whole screen renders', () => {
  it('draws the ported design over the running fixture', () => {
    const store = storeShowing(runningFromTheStart());
    const markup = renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen)));
    const words = text(markup);

    // The design's own furniture.
    expect(words).toContain('shinro');
    // THE TITLE FOLLOWS THE MODE. This fixture is a SIMULATION runtime, so the screen is a rehearsal and never headed
    // "Live demonstration" over a banner that says no robot was involved.
    expect(words).toContain('Rehearsal');
    expect(words).not.toContain('Live demonstration');
    expect(words).toContain('Stop ends the program in software');
    // THE DESIGN'S FIVE STEPS. They are a presentation over the controller's own twenty-three states, not a rename of
    // them: the state machine is untouched and its states stay in the Connections engineering reference.
    for (const step of ['Find', 'Pick', 'Carry', 'Place', 'Return']) {
      expect(words).toContain(step);
    }
    // The six-phase vocabulary does not appear on the operator surface any more.
    expect(words).not.toContain('Verify grip');
    // Both cameras, each named.
    expect(words).toContain('wrist');
    expect(words).toContain('front');
    // The one cube, and a rehearsal's count is never a placement.
    expect(words).toContain('of 1 cube recorded done');
    expect(words).not.toContain('cube placed');
    // WHICH LEKIWI: a synthetic runtime has no robot by construction, and it says so rather than naming the desktop
    // runtime's session as if it were one.
    expect(words).toContain('No robot: laptop rehearsal');
    expect(words).not.toContain(String(running().runtime_session));
    // THE CAKE PANEL IS THE ROBOT'S. With no robot reading it says it has read none; it never draws the laptop
    // resident, the synthetic ladder, or a slot count, and never says "on this laptop".
    expect(words).toContain('Robot not read');
    expect(words).not.toContain('on this laptop');
    expect(words).not.toContain('integration ladder');
    expect(words).not.toContain('Not connected to this task');
    expect(words).not.toContain('Software the runtime reports');
    expect(words).not.toContain('Plan digest');
    expect(words).not.toContain('Status surface version');
    // THE DRAWER ENTRY POINTS: the header's Diagnostics, which opens the focused four-tab drawer, and Connections,
    // History, Logs and Evidence beside the panels they belong to. Field bindings is gone.
    // DELIBERATELY CHANGED: this asserted "Diagnostics" ABSENT, when the design review renamed the header button to
    // Connections. The operator's requirement names the drawer "the focused Diagnostics drawer" and the lead's item 6
    // makes the header button read Diagnostics; the old Diagnostics drawer BODY stays gone, which the absence of its
    // integration ladder and panels above still pins.
    expect(words).toContain('Diagnostics');
    expect(words).toContain('Connections');
    expect(words).toContain('History');
    expect(words).toContain('Logs');
    expect(words).toContain('Evidence');
    expect(words).not.toContain('Field bindings');
    // OBSERVE AND RETURN ARM ARE IN THE WORKSPACE, with their scope sentences, and Start is still in the header only.
    expect(words).toContain('Observe scene');
    expect(words).toContain('The robot does not move.');
    expect(words).toContain('Return arm');
    expect(words).toContain('Moves the ARM only');
    expect(words).not.toContain('Start pick & place');
    // And one line of telemetry, which says unavailable rather than printing a number nobody measured.
    expect(words).toContain('Telemetry');
    expect(words).toContain('arm and base unavailable');
  });

  it('heads a live runtime as a live demonstration', () => {
    // A live runtime over HTTP: the in-page script is always a rehearsal, whatever its own snapshot says.
    const store = storeShowing({ ...runningFromTheStart(), data_mode: 'live' }, { sourceKind: 'live' });
    const words = text(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen))));
    expect(words).toContain('Live demonstration');
    expect(words).not.toContain('Rehearsal');
  });

  it('carries none of the prototype’s invented values', () => {
    const store = storeShowing(runningFromTheStart());
    const words = text(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen))));

    // Two cubes, and the cube identities that go with them.
    expect(words).not.toContain('Cube A');
    expect(words).not.toContain('Cube B');
    expect(words).not.toContain('of 2 cubes');
    // The invented capability request, its candidates and its receipt.
    expect(words).not.toContain('0.1.0-dev');
    expect(words).not.toContain('Validate & activate');
    expect(words).not.toContain('Activation receipt');
    expect(words).not.toContain('Unsigned build');
    // The invented inventory and its semantic versions.
    expect(words).not.toContain('Prototype inventory');
    expect(words).not.toContain('Pad & cube detection');
    expect(words).not.toContain('v1.2.0');
    expect(words).not.toContain('v0.9.3');
    // The walkthrough's made-up placement measurement, and the wording the real one must never use.
    expect(words).not.toContain('17.6');
    expect(words).not.toContain('footprint');
    // The prototype dock.
    expect(words).not.toContain('sample media');
    expect(words).not.toContain('Walkthrough');
  });

  it('never calls a stored frame live video, and draws no mission timer', () => {
    // No preview sample has arrived in this fold, so neither camera may be drawn as a moving picture. The prominent
    // mission timer is gone by ruling: the record has no finish time, so it was never a duration.
    const store = storeShowing(runningFromTheStart());
    const words = text(renderToStaticMarkup(createElement(StoreContext, { value: store }, createElement(Screen))));
    expect(words).not.toContain('Live video');
    expect(words).not.toContain('Mission time');
  });
});

// THE COLOUR DOWN THE SIDE OF THE RESULT CARD.
//
// It read `result !== null ? C.ok : ...`, which is the colour of a good outcome for EVERY finished run. The recorded
// failure of 2026-09-22 (outcome `failed`, exit 5, reason code `align_cube_not_detected`) drew a green rule.
describe('the result rule states what the run did', () => {
  const finished = (over: Partial<OutcomeView>) =>
    ({ reported: 'placed_new', confirmed: null, predicates: null, run_id: 'r-1', exit_status: 0, reason_code: null, ...over } as unknown as OutcomeView);
  const state = { running: false, ready: false, blocked: false };

  it('draws a reported failure as a failure and never as a good outcome', () => {
    for (const reported of ['failed', 'stopped', 'refused'] as const) {
      const rule = resultRule({ ...state, result: finished({ reported }) });
      expect(rule).toBe('failed');
      expect(resultRuleColour({ ...state, result: finished({ reported }) })).toBe(C.bad);
    }
  });

  it('reads the recorded align failure as a failure by all three of its own statements', () => {
    // Exactly the terminal record of console-20260922T194137Z-gellh5f4.
    const align = finished({ reported: 'failed', exit_status: 5, reason_code: 'align_cube_not_detected' });
    expect(resultFailed(align)).toBe(true);
    // And each statement alone is enough, because a record may carry only one of them.
    expect(resultFailed(finished({ exit_status: 5 }))).toBe(true);
    expect(resultFailed(finished({ reason_code: 'align_cube_not_detected' }))).toBe(true);
  });

  it('still draws a clean finished run as a good outcome', () => {
    expect(resultRule({ ...state, result: finished({}) })).toBe('succeeded');
    expect(resultFailed(finished({}))).toBe(false);
  });

  it('does not report `unknown` as a failure, because a command with no placement is written that way', () => {
    expect(resultFailed(finished({ reported: 'unknown' }))).toBe(false);
  });

  it('tells a run in progress apart from an idle console and from an idle runtime that answered ready', () => {
    expect(resultRule({ result: null, running: true, ready: true, blocked: false })).toBe('running');
    expect(resultRule({ result: null, running: false, ready: true, blocked: false })).toBe('ready');
    expect(resultRule({ result: null, running: false, ready: false, blocked: false })).toBe('idle');
    // Three different colours for three different facts, which the old conditional did not give.
    expect(new Set([RESULT_RULE_COLOUR.running, RESULT_RULE_COLOUR.ready, RESULT_RULE_COLOUR.idle]).size).toBe(3);
  });

  it('draws something standing in the way as the held colour, not as a ready one', () => {
    expect(resultRule({ result: null, running: false, ready: false, blocked: true })).toBe('blocked');
    expect(RESULT_RULE_COLOUR.blocked).toBe(C.wait);
  });
});

// THE WITHDRAWN FRONT-CAMERA CAUSE.
//
// The A/B/A refuted the attribution: the both-streams window had the SHORTEST longest gap and the worst was
// wrist-only, so the criterion carries no verdict either way and the measurement is the open question. The screen
// stated the withdrawn cause as the established reason the camera is off, and called the qualification failed.
describe('the front camera cover states an unresolved prerequisite and not a withdrawn cause', () => {
  // The qualification sentence is about the ROBOT's front camera, so it is drawn for a live runtime; a rehearsal has no
  // camera at all and says only that.
  const words = () =>
    text(renderToStaticMarkup(createElement(StoreContext, { value: storeShowing({ ...runningFromTheStart(), data_mode: 'live' }) }, createElement(Screen))));

  it('never claims that streaming both cameras lengthened the observation gaps', () => {
    const shown = words();
    expect(shown).not.toContain('lengthened');
    expect(shown).not.toContain('past the documented limit');
    // The bus-contention framing, withdrawn with it.
    expect(shown).not.toContain('contention');
    expect(shown).not.toContain('shares a USB hub');
  });

  it('never reports the qualification as failed, because it is unresolved', () => {
    const shown = words();
    expect(shown).not.toContain('timing qualification failed');
    expect(shown).not.toContain('qualification failed');
  });

  it('says the camera is held off for an unproven prerequisite, and points at Connections', () => {
    const shown = words();
    expect(shown).toContain('qualification unresolved');
    expect(shown).toContain('not because it was shown to cause harm');
    expect(shown).toContain('The measurements are in Connections');
    // DELIBERATELY NARROWED: the header button now reads Diagnostics (see above), so the whole screen contains the word.
    // What this pins is that the COVER no longer points at the old Diagnostics drawer.
    expect(shown).not.toContain('measurements are in Diagnostics');
    expect(shown).not.toContain('in Diagnostics');
  });
});

describe('a camera panel with no stream never claims a live camera', () => {
  const panelFor = (view: ViewState, preview: Record<string, unknown>) => {
    const base = storeShowing(view).getSnapshot();
    const snapshot: StoreSnapshot = {
      ...base,
      telemetry: { ...base.telemetry, previews: { wrist: sample(preview) }, preview_received_at_ms_by_role: { wrist: 1000 } },
    };
    return cameraPanel(snapshot, 'wrist', source([]), 1200);
  };
  const rehearsal = (): ViewState => ({ ...running(), data_mode: 'synthetic' });

  it('says NO CAMERA, not LIVE, over the rehearsal cover while the runtime reports frames', () => {
    const panel = panelFor(rehearsal(), { state: 'connected' });
    expect(panel.cover?.title).toBe('No camera in a rehearsal');
    expect(panel.word).toBe('NO CAMERA');
    expect(panel.word).not.toBe('LIVE');
  });

  it('shows a stall with the runtime reason and how long ago the runtime last received a frame', () => {
    const panel = panelFor(rehearsal(), { state: 'stalled', reason: 'no_sample_within_window', receive_age_ms: 6040 });
    expect(panel.word).toBe('STALLED');
    expect(panel.note).toBe('no_sample_within_window; the runtime last received a frame 6.0 s ago');
  });

  it('names a preview source that is not attached apart from a preview that was never configured', () => {
    expect(panelFor(running(), { state: 'disconnected', reason: 'preview_source_not_attached' }).word).toBe('NOT ATTACHED');
    expect(panelFor(running(), { state: 'disconnected', reason: 'preview_source_not_attached' }).note).toBe('preview_source_not_attached');
    expect(panelFor(running(), { state: 'disconnected', reason: 'preview_not_configured' }).word).toBe('NOT CONFIGURED');
  });
});
