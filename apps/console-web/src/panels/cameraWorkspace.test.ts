// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// The camera workspace and the pad map, actually rendered.
//
// The rules these cases hold down are the ones an operator is misled by when they break: a simulation that reports a
// camera fault it does not have, a second element pointed at the camera, a measurement drawn over the wrong picture,
// a rule drawn from a number this console made up, and an empty pad map that reads as an empty pad.
//
// It is a .ts file and builds its elements with createElement, because the vitest include is `src/**/*.test.ts`.

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SyntheticSource } from '../api/synthetic';
import type { ConsoleSource, PreviewSample } from '../api/types';
import { parsePerception } from '../model/observation';
import { initialState } from '../model/reducer';
import type { ObservationMeta, PredicateView, ViewState } from '../model/types';
import { ConsoleStore } from '../store/store';
import { DecisionFramePanel } from './DecisionFramePanel';
import { CameraClocksPanel, LiveViewPanel } from './LiveViewPanel';
import { PadScenePanel } from './PadScenePanel';

const NOW = 5_000_000;

function text(markup: string): string {
  return markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
}

function storeSnapshot(patch: Record<string, unknown>) {
  const store = new ConsoleStore(new SyntheticSource());
  return { ...store.getSnapshot(), ...patch } as ReturnType<ConsoleStore['getSnapshot']>;
}

/** A source that serves pictures the way the console API does: relative paths on this page, by observation id. */
const servingSource = {
  kind: 'live' as const,
  label: 'the runtime on this machine',
  frameUrl: (observationId: string, what: string, width: number) => `/api/observations/${observationId}/frame?what=${what}&w=${String(width)}`,
  previewUrl: (role: string) => `/api/preview.mjpg?role=${role}`,
} as unknown as ConsoleSource;

function preview(extra: Partial<PreviewSample> = {}): PreviewSample {
  return { role: 'wrist', seq: 4, capture_clock: null, receive_age_ms: 22, measured_fps: 9.1, available: true, reason: null, ...extra };
}

function telemetry(sample: PreviewSample | null, receivedAtMs: number | null = NOW) {
  return { tap: null, preview: sample, tap_received_at_ms: NOW, preview_received_at_ms: receivedAtMs };
}

function observation(extra: Partial<ObservationMeta> = {}): ObservationMeta {
  return {
    observation_id: 'obs-0009',
    source_role: 'wrist',
    media: 'frame',
    width_px: 640,
    height_px: 480,
    content_digest: 'sha256:0123456789abcdef',
    source_stamp: null,
    retained: true,
    seq: 44,
    t_wall_utc: '2026-09-21T10:00:00Z',
    ...extra,
  };
}

function predicate(extra: Partial<PredicateView> = {}): PredicateView {
  return {
    predicate: 'release_margin',
    value: true,
    state: 'evaluated',
    reason_code: null,
    rule_ref: null,
    rule_digest: null,
    threshold: 37.96,
    measured: 41.2,
    unit: 'mm',
    evidence: [],
    consumed_source_stamp: null,
    measurements: [],
    seq: 40,
    t_mono_ns: 40_000_000,
    clock_domain: 'laptop_monotonic',
    ...extra,
  };
}

function viewWith(extra: Partial<ViewState> = {}): ViewState {
  return { ...initialState(), ...extra };
}

describe('the camera card in a simulation that configured no camera', () => {
  const snapshot = storeSnapshot({
    view: viewWith(),
    telemetry: telemetry(preview({ state: 'disconnected', available: false, reason: 'preview_not_configured' })),
  });

  it('says there is nothing to connect to, and never calls it degraded', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'synthetic', source: servingSource, nowMs: NOW })));
    expect(words).toContain('No camera configured');
    expect(words).toContain('there is nothing to connect to');
    expect(words).toContain('preview_not_configured');
    expect(words.toLowerCase()).not.toContain('degraded');
  });

  it('marks the absence as the expected state of this fixture', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'synthetic', source: servingSource, nowMs: NOW })));
    expect(words).toContain('expected here');
  });

  it('opens no stream at all', () => {
    const markup = renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'synthetic', source: servingSource, nowMs: NOW }));
    expect(markup).not.toContain('preview.mjpg');
  });
});

describe('the camera card with a camera that is publishing', () => {
  const snapshot = storeSnapshot({
    view: viewWith({ observations: { 'obs-0009': observation() } }),
    telemetry: telemetry(preview({ state: 'connected', skipped_for_mission: 3, capture_age_reason: 'no_clock_relation_between_source_and_laptop' })),
  });

  it('points exactly one element at the camera, so a view is never a second owner of it', () => {
    const markup = renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW }));
    const images = markup.match(/<img[^>]*>/g) ?? [];
    expect(images).toHaveLength(1);
    expect(images[0]).toContain('/api/preview.mjpg?role=wrist');
  });

  // THE THREE AGES ARE NOW ASKED OF THE ENGINEERING CARD, because that is where the operator asked for the lengthy
  // clock explanations to live. Not one of these assertions was dropped or softened on the way: the same three
  // strings, the same clock names and the same yielding sentence are required, of the card that carries them now.
  it('keeps the capture age, the receive age and the arrival in this tab apart', () => {
    const words = text(renderToStaticMarkup(createElement(CameraClocksPanel, { snapshot, nowMs: NOW })));
    expect(words).toContain('capture age not known: the camera clock and the runtime clock have no measured relation');
    expect(words).toContain("22 ms ago, in the runtime's clock");
    expect(words).toContain('reached this tab 0 ms ago');
  });

  it('reads a preview the camera stood aside for as the camera serving the mission', () => {
    const words = text(renderToStaticMarkup(createElement(CameraClocksPanel, { snapshot, nowMs: NOW })));
    expect(words).toContain('3 preview(s) stood aside for a decision capture');
    expect(words).toContain('not a fault');
  });

  it('leaves the presentation card one honest line about the clocks, and no ages it cannot measure', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW })));
    expect(words).toContain('How long ago the camera took a picture is not a number this screen can state');
    expect(words).toContain('engineering tab');
    // The rows themselves are one tab over now, so their labels are not on this card at all.
    expect(words).not.toContain('reached this tab');
    expect(words).not.toContain("in the runtime's clock");
  });

  it('never lazy-loads the stream itself', () => {
    const markup = renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW }));
    const streamTag = markup.slice(markup.indexOf('<img'), markup.indexOf('>', markup.indexOf('<img')));
    expect(streamTag).toContain('preview.mjpg');
    expect(streamTag).not.toContain('loading="lazy"');
  });
});

describe('the camera card with no stream to show', () => {
  const snapshot = storeSnapshot({
    view: viewWith({ observations: { 'obs-0009': observation(), 'obs-0008': observation({ observation_id: 'obs-0008', seq: 40 }) } }),
    telemetry: telemetry(preview({ state: 'stalled', available: false, reason: 'no_frame_within_the_fresh_window' })),
  });

  it('shows the newest mission still, by its observation id, and says it is a still', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW })));
    // The words moved when the card stopped saying one sentence for three different absences: the still is named as
    // the newest frame the CONTROLLER stored, and the sentence now also says which source named it.
    expect(words).toContain('the newest frame the controller stored');
    expect(words).toContain('obs-0009');
    expect(words).toContain('It is a still, not video.');
    expect(words).toContain('1 still per 4 record(s)');
  });

  it('fetches that still through the console API by its id, and lazily', () => {
    const markup = renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW }));
    expect(markup).toContain('/api/observations/obs-0009/frame?what=raw&amp;w=640');
    expect(markup).toContain('loading="lazy"');
    expect(markup).not.toContain('preview.mjpg');
  });

  // The operator asked that a mission still carry its capture identity and its age unmistakably, since a still is all
  // there is to show until a preview path exists. These are the four things that make one still tellable from
  // another, and the word for what it is.
  it('leads with what the picture is, before any of its identity', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW })));
    expect(words).toContain('still, not video');
    expect(words.indexOf('still, not video')).toBeLessThan(words.indexOf('observation obs-0009'));
  });

  it('carries the capture identity: the observation, the camera role and the camera stamp the record held', () => {
    const stamped = storeSnapshot({
      view: viewWith({
        seq_cursor: 61,
        observations: {
          'obs-0009': observation({ source_stamp: { session: 'cam-session-7', seq: 812, sample_mono_ns: 5, clock_domain: 'pi_monotonic' } }),
        },
      }),
      telemetry: telemetry(preview({ state: 'stalled', available: false, reason: 'no_frame_within_the_fresh_window' })),
    });
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot: stamped, mode: 'live', source: servingSource, nowMs: NOW })));
    expect(words).toContain('observation obs-0009');
    expect(words).toContain('wrist camera');
    expect(words).toContain('camera session cam-session-7, sample 812');
  });

  it('says a record with no camera stamp carries none, rather than inventing a second identity for the picture', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW })));
    expect(words).toContain('the record carries no camera stamp for it');
  });

  it('places the still in records of the one ordered log, never as an age and never across two machines clocks', () => {
    const older = storeSnapshot({
      view: viewWith({ seq_cursor: 61, observations: { 'obs-0009': observation() } }),
      telemetry: telemetry(preview({ state: 'stalled', available: false, reason: 'no_frame_within_the_fresh_window' })),
    });
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot: older, mode: 'live', source: servingSource, nowMs: NOW })));
    // 61 folded, the still written at 44.
    expect(words).toContain('17 record(s) before the newest in this fold');
    expect(words).not.toContain('record(s) ago');
    // The recorded wall time is shown as the runtime wrote it and is never turned into a duration.
    expect(words).toContain('recorded at 2026-09-21T10:00:00Z');
  });

  it('reads a still the fold has not counted past yet as the newest one, never as a negative age', () => {
    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW })));
    expect(words).toContain('the newest record in this fold');
    // No negative count of records anywhere on the card. The recorded wall time has hyphens of its own, so the thing
    // being ruled out is a negative number before the word record, which is what a bare cursor subtraction produced.
    expect(words).not.toMatch(/-\d+ record/);
  });
});

describe('the camera card before anything has arrived', () => {
  const snapshot = storeSnapshot({ view: viewWith(), telemetry: telemetry(null, null) });

  it('says once that nothing has arrived, rather than four fields of the word unavailable', () => {
    // The clock rows are on the engineering card now; the one sentence for their absence went with them.
    const clocks = text(renderToStaticMarkup(createElement(CameraClocksPanel, { snapshot, nowMs: NOW })));
    expect(clocks).toContain('none has arrived, so there is no age to show in any clock');
    expect(clocks.match(/unavailable/g) ?? []).toHaveLength(0);

    const words = text(renderToStaticMarkup(createElement(LiveViewPanel, { snapshot, mode: 'live', source: servingSource, nowMs: NOW })));
    // NOTHING CAPTURED is now stated as that and only that, and it is allowed to be said only when both the fold and
    // the snapshot are empty, which is the case this fixture builds. The rule the line below holds down is unchanged:
    // a state this console KNOWS is never printed in the word it keeps for a value nobody reported.
    expect(words).toContain('No capture yet');
    expect(words).toContain('Nothing has been captured in this runtime session');
    expect(words.match(/unavailable/g) ?? []).toHaveLength(0);
  });
});

describe('the decision frame and the picture it is drawn over', () => {
  const capture = observation({ observation_id: 'obs-new', seq: 50 });
  const pinned = {
    observation_ids: ['obs-new'],
    primary_observation_id: 'obs-new',
    consumed_by: { by: 'predicate' as const, name: 'release_margin', seq: 50 },
    perception: null,
    capture,
    pinned_at_seq: 50,
  };
  const perception = parsePerception({
    pad_by_template: { pad_frame_valid: true },
    cube: { detected: true, box_px: [100, 100, 160, 160] },
  });

  it('draws nothing from a perception that was served for another observation', () => {
    const snapshot = storeSnapshot({
      view: viewWith({ decision_frame: pinned, seq_cursor: 52 }),
      frame: { observation_id: 'obs-old', record: null, perception, loading: false },
    });
    const words = text(renderToStaticMarkup(createElement(DecisionFramePanel, { snapshot, mode: 'live', source: servingSource })));
    expect(words).toContain('nothing measured in one frame is drawn over another');
    expect(words).toContain('obs-old');
    expect(words).toContain('obs-new');
  });

  it('draws the layers when the picture and the perception are the same observation', () => {
    const snapshot = storeSnapshot({
      view: viewWith({ decision_frame: pinned, seq_cursor: 50 }),
      frame: { observation_id: 'obs-new', record: null, perception, loading: false },
    });
    const words = text(renderToStaticMarkup(createElement(DecisionFramePanel, { snapshot, mode: 'live', source: servingSource })));
    expect(words).toContain('drawn from this frame');
    expect(words).not.toContain('nothing measured in one frame is drawn over another');
  });

  it('fetches the stored frame by its id through the console API, and lazily', () => {
    const snapshot = storeSnapshot({
      view: viewWith({ decision_frame: pinned, seq_cursor: 50 }),
      frame: { observation_id: 'obs-new', record: null, perception, loading: false },
    });
    const markup = renderToStaticMarkup(createElement(DecisionFramePanel, { snapshot, mode: 'live', source: servingSource }));
    expect(markup).toContain('/api/observations/obs-new/frame?what=raw&amp;w=800');
    expect(markup).toContain('loading="lazy"');
  });

  it('says the seq the frame was pinned at, and never an age in records or a duration across two clocks', () => {
    const snapshot = storeSnapshot({
      view: viewWith({ decision_frame: pinned, seq_cursor: 57 }),
      frame: { observation_id: 'obs-new', record: null, perception, loading: false },
    });
    const words = text(renderToStaticMarkup(createElement(DecisionFramePanel, { snapshot, mode: 'live', source: servingSource })));
    expect(words).toContain('pinned at seq 50');
    expect(words).not.toContain('record(s) ago');
    expect(words).not.toMatch(/\bago\b/);
  });

  it('stays empty rather than showing the newest capture when nothing has been pinned', () => {
    const words = text(renderToStaticMarkup(createElement(DecisionFramePanel, { snapshot: storeSnapshot({}), mode: 'live', source: servingSource })));
    expect(words).toContain('Nothing pinned yet');
    expect(words).toContain('the newest capture is not what anything was decided on');
  });
});

describe('the pad map', () => {
  const marks = [{ observation_id: 'obs-a', state: 'SEARCH', point_mm: [40, -12] as [number, number], seq: 20 }];

  it('is a compact inset with the numbers held back until it is opened', () => {
    const markup = renderToStaticMarkup(createElement(PadScenePanel, { view: viewWith(), marks }));
    expect(markup).toContain('viewBox="0 0 420.0');
    expect(text(markup)).toContain('open the map');
    expect(text(markup)).not.toContain('search anchor x');
  });

  it('opens to the larger drawing with the mark table under it', () => {
    const words = text(renderToStaticMarkup(createElement(PadScenePanel, { view: viewWith(), marks, open: true })));
    expect(words).toContain('search anchor');
    expect(words).toContain('x 40.0 mm, y -12.0 mm');
    expect(words).toContain('measured, from observation obs-a');
  });

  it('draws the admissible centre at the threshold the record carried, and names where it came from', () => {
    const view = viewWith({ predicates: [predicate({ threshold: 37.96, rule_ref: 'placement/v2' })] });
    const words = text(renderToStaticMarkup(createElement(PadScenePanel, { view, marks, open: true })));
    expect(words).toContain('38.0 mm');
    expect(words).toContain('release_margin decided at 38.0 mm under placement/v2');
  });

  it('draws no inset at all when no predicate in the run carried such a threshold', () => {
    const view = viewWith({ predicates: [predicate({ predicate: 'aligned', threshold: 6 })] });
    const words = text(renderToStaticMarkup(createElement(PadScenePanel, { view, marks, open: true })));
    expect(words).toContain('no predicate in this run carried a centre-to-edge threshold in millimetres');
    expect(words).toContain('this console holds no copy of the rule');
  });

  it('draws the absence when the run resolved no pad frame, instead of an outline over nothing', () => {
    const view = viewWith({ outcome: { ...initialState().outcome, reported: 'failed', reason_code: 'search_no_pad_frame' } });
    const words = text(renderToStaticMarkup(createElement(PadScenePanel, { view, marks: [] })));
    expect(words).toContain('no pad frame was resolved in this run');
    expect(words).toContain('No pad frame resolved');
    expect(words).toContain('search_no_pad_frame');
  });

  it('states that the plan is recorded as a mode and a travel rather than inventing a planned point', () => {
    const words = text(renderToStaticMarkup(createElement(PadScenePanel, { view: viewWith(), marks, open: true })));
    expect(words).toContain('planned release point');
    expect(words).toContain('not as a pad-frame point');
  });

  it('keeps the pad size as the configured one and says so', () => {
    const words = text(renderToStaticMarkup(createElement(PadScenePanel, { view: viewWith(), marks })));
    expect(words).toContain('415 x 295 mm');
  });
});
